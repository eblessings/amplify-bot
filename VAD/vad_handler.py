import torchaudio
from VAD.vad_iterator import VADIterator
from baseHandler import BaseHandler
import numpy as np
import torch
import logging

from utils.utils import int2float
from df.enhance import enhance, init_df

logger = logging.getLogger(__name__)

class VADHandler(BaseHandler):
    """
    Continuous, un-gated VAD. Emits each speech segment as a float32 numpy array.
    """

    def setup(
        self,
        thresh: float = 0.3,
        sample_rate: int = 16000,
        min_silence_ms: int = 1000,
        min_speech_ms: int = 500,
        max_speech_ms: float = float("inf"),
        speech_pad_ms: int = 30,
        audio_enhancement: bool = False,
    ):
        self.sample_rate = sample_rate
        self.min_speech_ms = min_speech_ms
        self.max_speech_ms = max_speech_ms
        self.model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad")
        self.iterator = VADIterator(
            self.model,
            threshold=thresh,
            sampling_rate=sample_rate,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )
        self.audio_enhancement = audio_enhancement
        if audio_enhancement:
            self.enhanced_model, self.df_state, _ = init_df()

    def process(self, audio_chunk: bytes):
        arr = np.frombuffer(audio_chunk, dtype=np.int16)
        flt = int2float(arr)
        vad_output = self.iterator(torch.from_numpy(flt))
        if vad_output:
            segment = torch.cat(vad_output).cpu().numpy()
            dur = len(segment) / self.sample_rate * 1000
            if not (self.min_speech_ms <= dur <= self.max_speech_ms):
                return
            if self.audio_enhancement:
                tensor = torch.from_numpy(segment)
                if self.sample_rate != self.df_state.sr():
                    tensor = torchaudio.functional.resample(
                        tensor, orig_freq=self.sample_rate, new_freq=self.df_state.sr()
                    )
                enhanced = enhance(self.enhanced_model, self.df_state, tensor.unsqueeze(0))
                segment = torchaudio.functional.resample(
                    enhanced, orig_freq=self.df_state.sr(), new_freq=self.sample_rate
                ).numpy().squeeze()
            yield segment

    @property
    def min_time_to_debug(self) -> float:
        return 1e-5
