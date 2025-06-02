import socket
import logging

logger = logging.getLogger(__name__)

class SocketReceiver:
    """
    Listens on TCP for incoming PCM audio bytes and enqueues them for STT.
    Uses `None` as shutdown sentinel.
    """

    def __init__(self, stop_event, queue_out, host="0.0.0.0", port=12345, chunk_size=1024):
        self.stop_event = stop_event
        self.queue_out = queue_out
        self.host = host
        self.port = port
        self.chunk_size = chunk_size

    def receive_full_chunk(self, conn, n):
        data = b""
        while len(data) < n:
            pkt = conn.recv(n - len(data))
            if not pkt:
                return None
            data += pkt
        return data

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(1)
        logger.info(f"SocketReceiver listening on {self.host}:{self.port}")
        conn, _ = sock.accept()
        logger.info("SocketReceiver: client connected")

        while not self.stop_event.is_set():
            chunk = self.receive_full_chunk(conn, self.chunk_size)
            if chunk is None:
                self.queue_out.put(None)
                break
            self.queue_out.put(chunk)

        conn.close()
        sock.close()
        logger.info("SocketReceiver: connection closed")
