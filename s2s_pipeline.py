import logging
import socket
import threading
from threading import Thread, Event
from queue import Queue
from time import perf_counter
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field
from copy import copy

import numpy as np
import torch
from nltk.tokenize import sent_tokenize
from rich.console import Console
from transformers import (
    AutoModelForCausalLM, 
    AutoModelForSpeechSeq2Seq, 
    AutoProcessor, 
    AutoTokenizer, 
    pipeline, 
    TextIteratorStreamer,
    HfArgumentParser
)
from parler_tts import (
    ParlerTTSForConditionalGeneration,
    ParlerTTSStreamer,
)

from utils import (
    VADIterator, 
    int2float,
)


# caching allows ~50% compilation time reduction
# see https://docs.google.com/document/d/1y5CRfMLdwEoF1nTk9q8qEu1mgMUuUtvhklPKJ2emLU8/edit#heading=h.o2asbxsrp1ma
CURRENT_DIR = Path(__file__).resolve().parent
os.environ["TORCHINDUCTOR_CACHE_DIR"] = os.path.join(CURRENT_DIR, "tmp") 
torch._inductor.config.fx_graph_cache = True
# mind about this parameter ! should be >= 2 * number of compiled models
torch._dynamo.config.cache_size_limit = 15

console = Console()

@dataclass
class ModuleArguments:
    log_level: str = field(
        default="info",
        metadata={
            "help": "Provide logging level. Example --log_level debug, default=warning."
        }
    )

class ThreadManager:
    def __init__(self, handlers):
        self.handlers = handlers
        self.threads = []

    def start(self):
        for handler in self.handlers:
            thread = threading.Thread(target=handler.run)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        for handler in self.handlers:
            handler.stop_event.set()
        for thread in self.threads:
            thread.join()

class BaseHandler:
    def __init__(self, stop_event, queue_in, queue_out, setup_args=(), setup_kwargs={}):
        self.stop_event = stop_event
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.setup(*setup_args, **setup_kwargs)
        self._times = []

    def setup(self):
        pass

    def process(self):
        raise NotImplementedError

    def run(self):
        while not self.stop_event.is_set():
            input = self.queue_in.get()
            if isinstance(input, bytes) and input == b'END':
                # sentinelle signal to avoid queue deadlock
                logger.debug("Stopping thread")
                break
            start_time = perf_counter()
            for output in self.process(input):
                self._times.append(perf_counter() - start_time)
                logger.debug(f"{self.__class__.__name__}: {self.last_time: .3f} s")
                self.queue_out.put(output)
                start_time = perf_counter()

        self.cleanup()
        self.queue_out.put(b'END')

    @property
    def last_time(self):
        return self._times[-1]

    def cleanup(self):
        pass


@dataclass
class SocketReceiverArguments:
    recv_host: str = field(
        default="localhost",
        metadata={
            "help": "The host IP ddress for the socket connection. Default is '0.0.0.0' which binds to all "
                    "available interfaces on the host machine."
        }
    )
    recv_port: int = field(
        default=12345,
        metadata={
            "help": "The port number on which the socket server listens. Default is 12346."
        }
    )
    chunk_size: int = field(
        default=1024,
        metadata={
            "help": "The size of each data chunk to be sent or received over the socket. Default is 1024 bytes."
        }
    )


class SocketReceiver:
    def __init__(
        self, 
        stop_event,
        queue_out,
        should_listen,
        host='0.0.0.0', 
        port=12345,
        chunk_size=1024
    ):  
        self.stop_event = stop_event
        self.queue_out = queue_out
        self.should_listen = should_listen
        self.chunk_size=chunk_size
        self.host = host
        self.port = port

    def receive_full_chunk(self, conn, chunk_size):
        data = b''
        while len(data) < chunk_size:
            packet = conn.recv(chunk_size - len(data))
            if not packet:
                # connection closed
                return None  
            data += packet
        return data

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(1)
        logger.info('Receiver waiting to be connected...')
        self.conn, _ = self.socket.accept()
        logger.info("receiver connected")

        self.should_listen.set()
        while not self.stop_event.is_set():
            audio_chunk = self.receive_full_chunk(self.conn, self.chunk_size)
            if audio_chunk is None:
                # connection closed
                self.queue_out.put(b'END')
                break
            if self.should_listen.is_set():
                self.queue_out.put(audio_chunk)
        self.conn.close()
        logger.info("Receiver closed")


@dataclass
class SocketSenderArguments:
    send_host: str = field(
        default="localhost",
        metadata={
            "help": "The host IP address for the socket connection. Default is '0.0.0.0' which binds to all "
                    "available interfaces on the host machine."
        }
    )
    send_port: int = field(
        default=12346,
        metadata={
            "help": "The port number on which the socket server listens. Default is 12346."
        }
    )

            
class SocketSender:
    def __init__(
        self, 
        stop_event,
        queue_in,
        host='0.0.0.0', 
        port=12346
    ):
        self.stop_event = stop_event
        self.queue_in = queue_in
        self.host = host
        self.port = port
        

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(1)
        logger.info('Sender waiting to be connected...')
        self.conn, _ = self.socket.accept()
        logger.info("sender connected")

        while not self.stop_event.is_set():
            audio_chunk = self.queue_in.get()
            self.conn.sendall(audio_chunk)
            if isinstance(audio_chunk, bytes) and audio_chunk == b'END':
                break
        self.conn.close()
        logger.info("Sender closed")


@dataclass
class VADHandlerArguments:
    thresh: float = field(
        default=0.3,
        metadata={
            "help": "The threshold value for voice activity detection (VAD). Values typically range from 0 to 1, with higher values requiring higher confidence in speech detection."
        }
    )
    sample_rate: int = field(
        default=16000,
        metadata={
            "help": "The sample rate of the audio in Hertz. Default is 16000 Hz, which is a common setting for voice audio."
        }
    )
    min_silence_ms: int = field(
        default=250,
        metadata={
            "help": "Minimum length of silence intervals to be used for segmenting speech. Measured in milliseconds. Default is 1000 ms."
        }
    )
    min_speech_ms: int = field(
        default=500,
        metadata={
            "help": "Minimum length of speech segments to be considered valid speech. Measured in milliseconds. Default is 500 ms."
        }
    )
    max_speech_ms: float = field(
        default=float('inf'),
        metadata={
            "help": "Maximum length of continuous speech before forcing a split. Default is infinite, allowing for uninterrupted speech segments."
        }
    )
    speech_pad_ms: int = field(
        default=30,
        metadata={
            "help": "Amount of padding added to the beginning and end of detected speech segments. Measured in milliseconds. Default is 30 ms."
        }
    )


class VADHandler(BaseHandler):
    def setup(
            self, 
            should_listen,
            thresh=0.3, 
            sample_rate=16000, 
            min_silence_ms=1000,
            min_speech_ms=500, 
            max_speech_ms=float('inf'),
            speech_pad_ms=30,

        ):
        self._should_listen = should_listen
        self._sample_rate = sample_rate
        self._min_silence_ms = min_silence_ms
        self._min_speech_ms = min_speech_ms
        self._max_speech_ms = max_speech_ms
        self.model, _ = torch.hub.load('snakers4/silero-vad', 'silero_vad')
        self.iterator = VADIterator(
            self.model,
            threshold=thresh,
            sampling_rate=sample_rate,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )

    def process(self, audio_chunk):
        audio_int16 = np.frombuffer(audio_chunk, dtype=np.int16)
        audio_float32 = int2float(audio_int16)
        vad_output = self.iterator(torch.from_numpy(audio_float32))
        if vad_output is not None:
            logger.debug("VAD: end of speech detected")
            array = torch.cat(vad_output).cpu().numpy()
            duration_ms = len(array) / self._sample_rate * 1000
            if duration_ms < self._min_speech_ms or duration_ms > self._max_speech_ms:
                logger.debug(f"audio input of duration: {len(array) / self._sample_rate}s, skipping")
            else:
                self._should_listen.clear()
                logger.debug("Stop listening")
                yield array


@dataclass
class WhisperSTTHandlerArguments:
    stt_model_name: str = field(
        default="distil-whisper/distil-large-v3",
        metadata={
            "help": "The pretrained Whisper model to use. Default is 'distil-whisper/distil-large-v3'."
        }
    )
    stt_device: str = field(
        default="cuda",
        metadata={
            "help": "The device type on which the model will run. Default is 'cuda' for GPU acceleration."
        }
    )
    stt_torch_dtype: str = field(
        default="float16",
        metadata={
            "help": "The PyTorch data type for the model and input tensors. One of `float32` (full-precision), `float16` or `bfloat16` (both half-precision)."
        } 
    )
    stt_compile_mode: str = field(
        default=None,
        metadata={
            "help": "Compile mode for torch compile. Either 'default', 'reduce-overhead' and 'max-autotune'. Default is None (no compilation)"
        }
    )
    stt_gen_max_new_tokens: int = field(
        default=128,
        metadata={
            "help": "The maximum number of new tokens to generate. Default is 128."
        }
    )
    stt_gen_num_beams: int = field(
        default=1,
        metadata={
            "help": "The number of beams for beam search. Default is 1, implying greedy decoding."
        }
    )
    stt_gen_return_timestamps: bool = field(
        default=False,
        metadata={
            "help": "Whether to return timestamps with transcriptions. Default is False."
        }
    )
    stt_gen_task: str = field(
        default="transcribe",
        metadata={
            "help": "The task to perform, typically 'transcribe' for transcription. Default is 'transcribe'."
        }
    )
    stt_gen_language: str = field(
        default="en",
        metadata={
            "help": "The language of the speech to transcribe. Default is 'en' for English."
        }
    )


class WhisperSTTHandler(BaseHandler):
    def setup(
            self,
            model_name="distil-whisper/distil-large-v3",
            device="cuda",  
            torch_dtype="float16",  
            compile_mode=None,
            gen_kwargs={}
        ): 
        self.compile_mode=compile_mode
        self.processor = AutoProcessor.from_pretrained(model_name)
        self.device = device
        self.torch_dtype = getattr(torch, torch_dtype)
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_name,
            torch_dtype=self.torch_dtype,
        ).to(device)
        self.gen_kwargs = gen_kwargs

        # compile
        if self.compile_mode:
            self.model.generation_config.cache_implementation = "static"
            self.model.forward = torch.compile(self.model.forward, mode=self.compile_mode, fullgraph=True)
        self.warmup()
    
    def prepare_model_inputs(self, spoken_prompt):
        input_features = self.processor(
            spoken_prompt, sampling_rate=16000, return_tensors="pt"
        ).input_features
        input_features = input_features.to(self.device, dtype=self.torch_dtype)
        return input_features
        
    def warmup(self):
        # 2 warmup steps for no compile or compile mode with CUDA graphs capture 
        n_steps = 1 if self.compile_mode == "default" else 2
        logger.info(f"Warming up {self.__class__.__name__}")
        dummy_input = torch.randn(
            (1,  self.model.config.num_mel_bins, 3000),
            dtype=self.torch_dtype,
            device=self.device
        ) 
        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        torch.cuda.synchronize()
        if self.compile_mode not in (None, "default"):
            # generating more tokens than previously will trigger CUDA graphs capture
            # one should warmup with a number of generated tokens above max tokens targeted for subsequent generation
            warmup_gen_kwargs = {
                "min_new_tokens": self.gen_kwargs["max_new_tokens"],
                "max_new_tokens": self.gen_kwargs["max_new_tokens"],
                **self.gen_kwargs
            }
        else:
            warmup_gen_kwargs = self.gen_kwargs

        start_event.record()
        for _ in range(n_steps):
            _ = self.model.generate(dummy_input, **warmup_gen_kwargs)
        end_event.record()
        torch.cuda.synchronize()
        logger.info(f"{self.__class__.__name__}:  warmed up! time: {start_event.elapsed_time(end_event) * 1e-3:.3f} s")

    def process(self, spoken_prompt):
        global pipeline_start
        pipeline_start = perf_counter()
        input_features = self.processor(
            spoken_prompt, sampling_rate=16000, return_tensors="pt"
        ).input_features
        input_features = input_features.to(self.device, dtype=self.torch_dtype)
        logger.debug("infering whisper...")
        pred_ids = self.model.generate(input_features, **self.gen_kwargs)
        pred_text = self.processor.batch_decode(
            pred_ids, skip_special_tokens=True,
            decode_with_timestamps=False
        )[0]
        logger.debug("finished whisper inference")
        console.print(f"[yellow]USER: {pred_text}")
        yield pred_text


@dataclass
class LanguageModelHandlerArguments:
    lm_model_name: str = field(
        default="microsoft/Phi-3-mini-4k-instruct",
        metadata={
            "help": "The pretrained language model to use. Default is 'microsoft/Phi-3-mini-4k-instruct'."
        }
    )
    lm_device: str = field(
        default="cuda",
        metadata={
            "help": "The device type on which the model will run. Default is 'cuda' for GPU acceleration."
        }
    )
    lm_torch_dtype: str = field(
        default="float16",
        metadata={
            "help": "The PyTorch data type for the model and input tensors. One of `float32` (full-precision), `float16` or `bfloat16` (both half-precision)."
        }
    )
    user_role: str = field(
        default="user",
        metadata={
            "help": "Role assigned to the user in the chat context. Default is 'user'."
        }
    )
    init_chat_role: str = field(
        default=None,
        metadata={
            "help": "Initial role for setting up the chat context. Default is 'system'."
        }
    )
    init_chat_prompt: str = field(
        default="You are a helpful AI assistant.",
        metadata={
            "help": "The initial chat prompt to establish context for the language model. Default is 'You are a helpful AI assistant.'"
        }
    )
    lm_gen_max_new_tokens: int = field(
        default=128,
        metadata={"help": "Maximum number of new tokens to generate in a single completion. Default is 128."}
    )
    lm_gen_temperature: float = field(
        default=0.0,
        metadata={"help": "Controls the randomness of the output. Set to 0.0 for deterministic (repeatable) outputs. Default is 0.0."}
    )
    lm_gen_do_sample: bool = field(
        default=False,
        metadata={"help": "Whether to use sampling; set this to False for deterministic outputs. Default is False."}
    )


class LanguageModelHandler(BaseHandler):
    def setup(
            self,
            model_name="microsoft/Phi-3-mini-4k-instruct",
            device="cuda", 
            torch_dtype="float16",
            gen_kwargs={},
            user_role="user",
            init_chat_role=None, 
            init_chat_prompt="You are a helpful AI assistant.",
        ):
        self.torch_dtype = getattr(torch, torch_dtype)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch_dtype,
            trust_remote_code=True
        ).to(device)
        self.device = device
        self.pipe = pipeline( 
            "text-generation", 
            model=self.model, 
            tokenizer=self.tokenizer, 
        ) 
        self.streamer = TextIteratorStreamer(
            self.tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        self.chat = []
        if init_chat_role:
            if not init_chat_prompt:
                raise ValueError(f"An initial promt needs to be specified when setting init_chat_role.")
            self.chat.append(
                {"role": init_chat_role, "content": init_chat_prompt}
            )
        self.gen_kwargs = {
            "streamer": self.streamer,
            "return_full_text": False,
            **gen_kwargs
        }
        self.user_role = user_role
        self.warmup()

    def warmup(self):
        # 2 warmup steps for no compile or compile mode with CUDA graphs capture 
        n_steps = 2
        logger.info(f"Warming up {self.__class__.__name__}")
        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        torch.cuda.synchronize()

        dummy_input_text = "Write me a poem about Machine Learning."
        dummy_chat = [{"role": self.user_role, "content": dummy_input_text}]
        warmup_gen_kwargs = {
            "min_new_tokens": self.gen_kwargs["max_new_tokens"],
            "max_new_tokens": self.gen_kwargs["max_new_tokens"],
            **self.gen_kwargs
        }

        start_event.record()
        for _ in range(n_steps):
            thread = Thread(target=self.pipe, args=(dummy_chat,), kwargs=warmup_gen_kwargs)
            thread.start()
            for _ in self.streamer: 
                pass
                
        end_event.record()
        torch.cuda.synchronize()
        logger.info(f"{self.__class__.__name__}:  warmed up! time: {start_event.elapsed_time(end_event) * 1e-3:.3f} s")

    def process(self, prompt):
        self.chat.append(
            {"role": self.user_role, "content": prompt}
        )
        thread = Thread(target=self.pipe, args=(self.chat,), kwargs=self.gen_kwargs)
        thread.start()
        generated_text, printable_text = "", ""
        logger.debug("infering language model...")
        for new_text in self.streamer:
            generated_text += new_text
            printable_text += new_text
            sentences = sent_tokenize(printable_text)
            if len(sentences) > 1:
                yield(sentences[0])
                printable_text = new_text
        self.chat.append(
            {"role": "assistant", "content": generated_text}
        )
        # don't forget last sentence
        yield printable_text


@dataclass
class ParlerTTSHandlerArguments:
    tts_model_name: str = field(
        default="ylacombe/parler-tts-mini-jenny-30H",
        metadata={
            "help": "The pretrained TTS model to use. Default is 'ylacombe/parler-tts-mini-jenny-30H'."
        }
    )
    tts_device: str = field(
        default="cuda",
        metadata={
            "help": "The device type on which the model will run. Default is 'cuda' for GPU acceleration."
        }
    )
    tts_torch_dtype: str = field(
        default="float16",
        metadata={
            "help": "The PyTorch data type for the model and input tensors. One of `float32` (full-precision), `float16` or `bfloat16` (both half-precision)."
        }
    )
    gen_kwargs: dict = field(
        default_factory=dict,
        metadata={
            "help": "Additional keyword arguments to pass to the model's generate method. Use this to customize generation settings."
        }
    )
    description: str = field(
        default=(
            "A female speaker with a slightly low-pitched voice delivers her words quite expressively, in a very confined sounding environment with clear audio quality. "
            "She speaks very fast."
        ),
        metadata={
            "help": "Description of the speaker's voice and speaking style to guide the TTS model."
        }
    )
    play_steps_s: float = field(
        default=0.2,
        metadata={
            "help": "The time interval in seconds for playing back the generated speech in steps. Default is 0.5 seconds."
        }
    )


class ParlerTTSHandler(BaseHandler):
    def setup(
            self,
            should_listen,
            model_name="ylacombe/parler-tts-mini-jenny-30H",
            device="cuda", 
            torch_dtype="float16",
            gen_kwargs={},
            description=(
                "A female speaker with a slightly low-pitched voice delivers her words quite expressively, in a very confined sounding environment with clear audio quality. "
                "She speaks very fast."
            ),
            play_steps_s=0.5
        ):
        torch_dtype = getattr(torch, torch_dtype)
        self._should_listen = should_listen
        self.description_tokenizer = AutoTokenizer.from_pretrained(model_name) 
        self.prompt_tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = ParlerTTSForConditionalGeneration.from_pretrained(
            model_name,
            torch_dtype=torch_dtype
        ).to(device)
        self.device = device
        self.torch_dtype = torch_dtype

        tokenized_description = self.description_tokenizer(description, return_tensors="pt")
        input_ids = tokenized_description.input_ids.to(self.device)
        attention_mask = tokenized_description.attention_mask.to(self.device)

        self.gen_kwargs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            **gen_kwargs
        }
        
        framerate = self.model.audio_encoder.config.frame_rate
        self.play_steps = int(framerate * play_steps_s)

    def process(self, lm_sentence):
        console.print(f"[green]ASSISTANT: {lm_sentence}")
        tokenized_prompt = self.prompt_tokenizer(lm_sentence, return_tensors="pt")
        prompt_input_ids = tokenized_prompt.input_ids.to(self.device)
        prompt_attention_mask = tokenized_prompt.attention_mask.to(self.device)

        streamer = ParlerTTSStreamer(self.model, device=self.device, play_steps=self.play_steps)
        tts_gen_kwargs = {
            "prompt_input_ids": prompt_input_ids,
            "prompt_attention_mask": prompt_attention_mask,
            "streamer": streamer,
            **self.gen_kwargs
        }

        torch.manual_seed(0)
        thread = Thread(target=self.model.generate, kwargs=tts_gen_kwargs)
        thread.start()

        for i, audio_chunk in enumerate(streamer):
            if i == 0:
                logger.info(f"Time to first audio: {perf_counter() - pipeline_start:.3f}")
            audio_chunk = np.int16(audio_chunk * 32767)
            yield audio_chunk

        self._should_listen.set()


def prepare_args(args, prefix):
    gen_kwargs = {}
    for key in copy(args.__dict__):
        if key.startswith(prefix):
            value = args.__dict__.pop(key)
            new_key = key[len(prefix) + 1:]  # Remove prefix and underscore
            if new_key.startswith("gen_"):
                gen_kwargs[new_key[4:]] = value  # Remove 'gen_' and add to dict
            else:
                args.__dict__[new_key] = value

    args.__dict__["gen_kwargs"] = gen_kwargs


def main():
    parser = HfArgumentParser((
        ModuleArguments,
        SocketReceiverArguments, 
        SocketSenderArguments,
        VADHandlerArguments,
        WhisperSTTHandlerArguments,
        LanguageModelHandlerArguments,
        ParlerTTSHandlerArguments,
    ))

    # 0. Parse CLI arguments
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # Parse configurations from a JSON file if specified
        (
            module_kwargs,
            socket_receiver_kwargs, 
            socket_sender_kwargs, 
            vad_handler_kwargs, 
            whisper_stt_handler_kwargs, 
            language_model_handler_kwargs, 
            parler_tts_handler_kwargs,
        ) = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    else:
        # Parse arguments from command line if no JSON file is provided
        (
            module_kwargs,
            socket_receiver_kwargs, 
            socket_sender_kwargs, 
            vad_handler_kwargs, 
            whisper_stt_handler_kwargs, 
            language_model_handler_kwargs, 
            parler_tts_handler_kwargs,
        ) = parser.parse_args_into_dataclasses()

    global logger
    logging.basicConfig(
        level=module_kwargs.log_level.upper(),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    logger = logging.getLogger(__name__)

    # torch compile logs
    if module_kwargs.log_level == "debug":
        torch._logging.set_logs(graph_breaks=True, recompiles=True, cudagraphs=True)

    prepare_args(whisper_stt_handler_kwargs, "stt")
    prepare_args(language_model_handler_kwargs, "lm")
    prepare_args(parler_tts_handler_kwargs, "tts") 

    stop_event = Event()
    should_listen = Event()
    recv_audio_chunks_queue = Queue()
    send_audio_chunks_queue = Queue()
    spoken_prompt_queue = Queue() 
    text_prompt_queue = Queue()
    lm_response_queue = Queue()
    
    vad = VADHandler(
        stop_event,
        queue_in=recv_audio_chunks_queue,
        queue_out=spoken_prompt_queue,
        setup_args=(should_listen,),
        setup_kwargs=vars(vad_handler_kwargs),
    )
    stt = WhisperSTTHandler(
        stop_event,
        queue_in=spoken_prompt_queue,
        queue_out=text_prompt_queue,
        setup_kwargs=vars(whisper_stt_handler_kwargs),
    )
    lm = LanguageModelHandler(
        stop_event,
        queue_in=text_prompt_queue,
        queue_out=lm_response_queue,
        setup_kwargs=vars(language_model_handler_kwargs),
    )
    tts = ParlerTTSHandler(
        stop_event,
        queue_in=lm_response_queue,
        queue_out=send_audio_chunks_queue,
        setup_args=(should_listen,),
        setup_kwargs=vars(parler_tts_handler_kwargs),
    )  

    recv_handler = SocketReceiver(
        stop_event, 
        recv_audio_chunks_queue, 
        should_listen,
        host=socket_receiver_kwargs.recv_host,
        port=socket_receiver_kwargs.recv_port,
        chunk_size=socket_receiver_kwargs.chunk_size,
    )

    send_handler = SocketSender(
        stop_event, 
        send_audio_chunks_queue,
        host=socket_sender_kwargs.send_host,
        port=socket_sender_kwargs.send_port,
        )

    try:
        pipeline_manager = ThreadManager([vad, tts, lm, stt, recv_handler, send_handler])
        pipeline_manager.start()

    except KeyboardInterrupt:
        pipeline_manager.stop()
    
if __name__ == "__main__":
    main()
