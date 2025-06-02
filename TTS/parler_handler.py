import logging
from baseHandler import BaseHandler
from TTS.api import TTS as ParlerTTS  # adjust import if needed

logger = logging.getLogger(__name__)


class ParlerTTSHandler(BaseHandler):
    """
    Parler-TTS handler in full-duplex: streams PCM blocks directly,
    no gating or should_listen.
    """

    def setup(self, model_name="tts-parler", gen_kwargs: dict = None, blocksize: int = 512):
        self.blocksize = blocksize
        self.model = ParlerTTS(model=model_name, **(gen_kwargs or {}))

    def process(self, text_chunk):
        # ParlerTTS returns an iterator of numpy arrays
        for audio in self.model.stream(text_chunk):
            pcm = (audio * 32767).astype("int16")
            for i in range(0, len(pcm), self.blocksize):
                chunk = pcm[i : i + self.blocksize]
                if len(chunk) < self.blocksize:
                    chunk = np.pad(chunk, (0, self.blocksize - len(chunk)))
                yield chunk
