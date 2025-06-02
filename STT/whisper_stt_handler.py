import logging
import numpy as np
import torch
from baseHandler import BaseHandler
from transformers import WhisperProcessor, WhisperForConditionalGeneration

logger = logging.getLogger(__name__)

class WhisperSTTHandler(BaseHandler):
    """
    Un-gated Whisper via Transformers. Emits only non-empty transcripts.
    """

    def setup(self, model_name="openai/whisper-small", device="cpu",
              torch_dtype="float32", gen_kwargs=None, **kwargs):
        dtype = getattr(torch, torch_dtype)
        self.processor = WhisperProcessor.from_pretrained(model_name)
        self.model = WhisperForConditionalGeneration.from_pretrained(
            model_name, torch_dtype=dtype
        ).to(device)
        # remove forced_decoder_ids to avoid conflicts
        self.model.generation_config.forced_decoder_ids = None
        self.model.generation_config.task = "transcribe"
        self.gen_kwargs = gen_kwargs or {}
        if not self.gen_kwargs.get("do_sample", True):
            self.gen_kwargs.pop("temperature", None)

    def process(self, audio_chunk: bytes):
        arr = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32)/32768.0
        inputs = self.processor(arr, sampling_rate=16000,
                                 return_tensors="pt", padding="longest",
                                 return_attention_mask=True)
        feats = inputs["input_features"].to(self.model.device, dtype=self.model.dtype)
        mask = inputs["attention_mask"].to(self.model.device)
        ids = self.model.generate(feats, attention_mask=mask, **self.gen_kwargs)
        text = self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        if text:
            yield text
