import logging
import os
import sys
from copy import copy
from pathlib import Path
from queue import Queue
from threading import Event
from sys import platform

import torch
import nltk
from rich.console import Console
from transformers import HfArgumentParser

from VAD.vad_handler import VADHandler
from STT.whisper_stt_handler import WhisperSTTHandler
from LLM.language_model import LanguageModelHandler
from TTS.melo_handler import MeloTTSHandler
from connections.socket_receiver import SocketReceiver
from connections.socket_sender import SocketSender
from connections.local_audio_streamer import LocalAudioStreamer
from utils.thread_manager import ThreadManager

from arguments_classes.module_arguments import ModuleArguments
from arguments_classes.socket_receiver_arguments import SocketReceiverArguments
from arguments_classes.socket_sender_arguments import SocketSenderArguments
from arguments_classes.vad_arguments import VADHandlerArguments
from arguments_classes.whisper_stt_arguments import WhisperSTTHandlerArguments
from arguments_classes.faster_whisper_stt_arguments import FasterWhisperSTTHandlerArguments
from arguments_classes.paraformer_stt_arguments import ParaformerSTTHandlerArguments
from arguments_classes.language_model_arguments import LanguageModelHandlerArguments
from arguments_classes.open_api_language_model_arguments import OpenApiLanguageModelHandlerArguments
from arguments_classes.mlx_language_model_arguments import MLXLanguageModelHandlerArguments
from arguments_classes.parler_tts_arguments import ParlerTTSHandlerArguments
from arguments_classes.melo_tts_arguments import MeloTTSHandlerArguments
from arguments_classes.chat_tts_arguments import ChatTTSHandlerArguments
from arguments_classes.facebookmms_tts_arguments import FacebookMMSTTSHandlerArguments

console = Console()
logging.getLogger("numba").setLevel(logging.WARNING)

# Ensure NLTK data
for pkg in ("punkt_tab", "averaged_perceptron_tagger_eng"):
    try:
        nltk.data.find(f"tokenizers/{pkg}")
    except:
        nltk.download(pkg)

# Torch-Inductor cache
CURRENT_DIR = Path(__file__).resolve().parent
os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(CURRENT_DIR / "tmp")


def rename_args(args, prefix):
    gen_kwargs = {}
    for key in copy(args.__dict__):
        if key.startswith(prefix):
            val = args.__dict__.pop(key)
            new_key = key[len(prefix) + 1:]
            if new_key.startswith("gen_"):
                gen_kwargs[new_key[4:]] = val
            else:
                args.__dict__[new_key] = val
    args.__dict__["gen_kwargs"] = gen_kwargs


def parse_arguments():
    parser = HfArgumentParser((
        ModuleArguments,
        SocketReceiverArguments,
        SocketSenderArguments,
        VADHandlerArguments,
        WhisperSTTHandlerArguments,
        ParaformerSTTHandlerArguments,
        FasterWhisperSTTHandlerArguments,
        LanguageModelHandlerArguments,
        OpenApiLanguageModelHandlerArguments,
        MLXLanguageModelHandlerArguments,
        ParlerTTSHandlerArguments,
        MeloTTSHandlerArguments,
        ChatTTSHandlerArguments,
        FacebookMMSTTSHandlerArguments,
    ))
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        return parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    return parser.parse_args_into_dataclasses()


def setup_logger(level: str):
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    if level.lower() == "debug":
        torch._logging.set_logs(graph_breaks=True, recompiles=True, cudagraphs=True)


def initialize_queues_and_events():
    return {
        "stop_event": Event(),
        "recv_q": Queue(),
        "send_q": Queue(),
        "spoken_q": Queue(),
        "text_q": Queue(),
        "lm_q": Queue(),
    }


def build_pipeline(
    module_kwargs,
    socket_recv_kwargs,
    socket_send_kwargs,
    vad_kwargs,
    whisper_kwargs,
    paraformer_kwargs,
    faster_whisper_kwargs,
    lm_kwargs,
    open_api_kwargs,
    mlx_kwargs,
    parler_kwargs,
    melo_kwargs,
    chat_tts_kwargs,
    fb_mms_kwargs,
    eq
):
    stop_event = eq["stop_event"]
    recv_q = eq["recv_q"]
    send_q = eq["send_q"]
    spoken_q = eq["spoken_q"]
    text_q = eq["text_q"]
    lm_q = eq["lm_q"]

    # Communication
    if module_kwargs.mode == "local":
        comms = [LocalAudioStreamer(recv_q, send_q)]
    else:
        comms = [
            SocketReceiver(stop_event, recv_q,
                           host=socket_recv_kwargs.recv_host,
                           port=socket_recv_kwargs.recv_port,
                           chunk_size=socket_recv_kwargs.chunk_size),
            SocketSender(stop_event, send_q,
                         host=socket_send_kwargs.send_host,
                         port=socket_send_kwargs.send_port),
        ]

    # VAD → STT → LM → TTS all concurrent
    vad = VADHandler(stop_event, recv_q, spoken_q,
                     setup_kwargs=vars(vad_kwargs))
    stt = WhisperSTTHandler(stop_event, spoken_q, text_q,
                             setup_kwargs=vars(whisper_kwargs))
    lm = LanguageModelHandler(stop_event, text_q, lm_q,
                               setup_kwargs=vars(lm_kwargs))
    tts = MeloTTSHandler(stop_event, lm_q, send_q,
                          setup_kwargs=vars(melo_kwargs))

    return ThreadManager([*comms, vad, stt, lm, tts])


def main():
    (
        module_kwargs,
        socket_recv_kwargs,
        socket_send_kwargs,
        vad_kwargs,
        whisper_kwargs,
        paraformer_kwargs,
        faster_whisper_kwargs,
        lm_kwargs,
        open_api_kwargs,
        mlx_kwargs,
        parler_kwargs,
        melo_kwargs,
        chat_tts_kwargs,
        fb_mms_kwargs,
    ) = parse_arguments()

    setup_logger(module_kwargs.log_level)

    # Rename gen_kwargs for each
    rename_args(whisper_kwargs, "stt")
    rename_args(faster_whisper_kwargs, "faster_whisper_stt")
    rename_args(paraformer_kwargs, "paraformer_stt")
    rename_args(lm_kwargs, "lm")
    rename_args(mlx_kwargs, "mlx_lm")
    rename_args(open_api_kwargs, "open_api")
    rename_args(parler_kwargs, "tts")
    rename_args(melo_kwargs, "melo")
    rename_args(chat_tts_kwargs, "chat_tts")
    rename_args(fb_mms_kwargs, "facebook_mms")

    eq = initialize_queues_and_events()
    pipeline = build_pipeline(
        module_kwargs,
        socket_recv_kwargs,
        socket_send_kwargs,
        vad_kwargs,
        whisper_kwargs,
        paraformer_kwargs,
        faster_whisper_kwargs,
        lm_kwargs,
        open_api_kwargs,
        mlx_kwargs,
        parler_kwargs,
        melo_kwargs,
        chat_tts_kwargs,
        fb_mms_kwargs,
        eq
    )

    try:
        pipeline.start()
    except KeyboardInterrupt:
        pipeline.stop()


if __name__ == "__main__":
    main()
