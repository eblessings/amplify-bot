import socket
import logging

logger = logging.getLogger(__name__)

class SocketSender:
    """
    Listens on TCP for a client, then sends PCM audio chunks from queue_in.
    Exits on `None` sentinel.
    """

    def __init__(self, stop_event, queue_in, host="0.0.0.0", port=12346):
        self.stop_event = stop_event
        self.queue_in = queue_in
        self.host = host
        self.port = port

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(1)
        logger.info(f"SocketSender listening on {self.host}:{self.port}")
        conn, _ = sock.accept()
        logger.info("SocketSender: client connected")

        while not self.stop_event.is_set():
            data = self.queue_in.get()
            if data is None:
                break
            try:
                conn.sendall(data)
            except (BrokenPipeError, ConnectionResetError):
                break

        conn.close()
        sock.close()
        logger.info("SocketSender: connection closed")
