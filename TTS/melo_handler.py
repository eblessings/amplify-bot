import tempfile, os, logging
from baseHandler import BaseHandler
import librosa, numpy as np
from melo.api import TTS

logger = logging.getLogger(__name__)
LANG2MELO = {
    "en": ("EN", "EN-BR"), "fr":("FR","FR"), "es":("ES","ES"),
    "zh":("ZH","ZH"), "ja":("JP","JP"), "ko":("KR","KR")
}

class MeloTTSHandler(BaseHandler):
    def setup(self, device="cpu", language="en", speaker_to_id="en",
              gen_kwargs=None, blocksize=512):
        self.device = device
        self.blocksize = blocksize
        lang, spk = LANG2MELO.get(language, LANG2MELO["en"])
        self.model = TTS(language=lang, device=device)
        self.speaker_id = self.model.hps.data.spk2id[spk]

    def process(self, text_chunk):
        if not isinstance(text_chunk, str):
            try: text_chunk = text_chunk.decode("utf-8")
            except: text_chunk = str(text_chunk)
        if not text_chunk: return
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        path = tmp.name; tmp.close()
        try:
            self.model.tts_to_file(text_chunk, self.speaker_id, path)
            audio, sr = librosa.load(path, sr=None)
            if sr!=16000:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
            pcm = (audio*32767).astype(np.int16)
        except Exception as e:
            logger.error(f"MeloTTS error: {e}")
            return
        finally:
            if os.path.exists(path):
                os.remove(path)
        for i in range(0,len(pcm),self.blocksize):
            chunk=pcm[i:i+self.blocksize]
            if len(chunk)<self.blocksize:
                chunk=np.pad(chunk,(0,self.blocksize-len(chunk)))
            yield chunk
