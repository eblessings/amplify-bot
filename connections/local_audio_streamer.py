from baseHandler import BaseHandler

class LocalAudioStreamer(BaseHandler):
    """
    In-process loopback: moves audio from input_queue to output_queue
    to simulate full-duplex locally. Uses `None` sentinel.
    """

    def setup(self):
        pass  # nothing to init

    def process(self, chunk):
        # chunk may be bytes or numpy array—just forward
        yield chunk
