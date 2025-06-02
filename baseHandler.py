import threading
import logging

logger = logging.getLogger(__name__)

class BaseHandler(threading.Thread):
    """
    Core handler abstraction. Pulls items from queue_in, processes them,
    and pushes results to queue_out. Uses `None` as shutdown sentinel.
    """

    def __init__(self, stop_event, queue_in, queue_out, setup_kwargs=None):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.setup_kwargs = setup_kwargs or {}
        self.setup(**self.setup_kwargs)

    def setup(self, **kwargs):
        """Optional per-handler initialization."""
        pass

    def process(self, item):
        """
        Override in subclasses. Should be a generator yielding outputs
        or return None / empty generator to do nothing.
        """
        raise NotImplementedError

    def run(self):
        while not self.stop_event.is_set():
            item = self.queue_in.get()  # blocks
            if item is None:
                # Shutdown sentinel
                break
            try:
                for out in self.process(item):
                    if out is None:
                        continue
                    self.queue_out.put(out)
            except Exception as e:
                logger.exception(f"{self.__class__.__name__} error: {e}")
        # signal downstream to stop
        self.queue_out.put(None)
