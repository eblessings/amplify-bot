#!/usr/bin/env python3
import socket
import threading
import time
from queue import Queue, Empty
from dataclasses import dataclass, field

import numpy as np
import sounddevice as sd
from transformers import HfArgumentParser


@dataclass
class ListenAndPlayArguments:
    send_rate: int = field(default=16000, metadata={"help":"Mic rate"})
    recv_rate: int = field(default=16000, metadata={"help":"Speaker rate"})
    chunk_size: int = field(default=1024, metadata={"help":"Frames per chunk"})
    host: str = field(default="localhost", metadata={"help":"Server host"})
    send_port: int = field(default=12345, metadata={"help":"Send port"})
    recv_port: int = field(default=12346, metadata={"help":"Recv port"})


def listen_and_play(send_rate, recv_rate, chunk_size, host, send_port, recv_port):
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    send_sock.connect((host, send_port))
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    recv_sock.connect((host, recv_port))
    print(f"Connected send=>{host}:{send_port}, recv=>{host}:{recv_port}")

    stop_event = threading.Event()
    send_q, recv_q = Queue(), Queue()

    def cb_send(indata, frames, time_info, status):
        if status:
            print(f"[mic] {status}")
        send_q.put(bytes(indata))

    def cb_recv(outdata, frames, time_info, status):
        if status:
            print(f"[spkr] {status}")
        try:
            data = recv_q.get_nowait()
            outdata[:len(data)] = data
            if len(data) < len(outdata):
                outdata[len(data):] = b"\x00" * (len(outdata)-len(data))
        except Empty:
            outdata[:] = b"\x00" * len(outdata)

    def sender():
        while not stop_event.is_set():
            data = send_q.get()
            if data is None:
                break
            try:
                send_sock.sendall(data)
            except:
                stop_event.set()
                break

    def receiver():
        size = chunk_size*2
        while not stop_event.is_set():
            try:
                data = recv_sock.recv(size)
                if not data:
                    stop_event.set()
                    break
                recv_q.put(data)
            except:
                stop_event.set()
                break

    threading.Thread(target=sender, daemon=True).start()
    threading.Thread(target=receiver, daemon=True).start()

    try:
        with sd.RawInputStream(samplerate=send_rate, channels=1,
                               dtype="int16", blocksize=chunk_size,
                               callback=cb_send), \
             sd.RawOutputStream(samplerate=recv_rate, channels=1,
                                dtype="int16", blocksize=chunk_size,
                                callback=cb_recv):
            print("Streaming... Ctrl+C to stop")
            while not stop_event.is_set():
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        send_sock.close()
        recv_sock.close()
        print("Connections closed.")


if __name__ == "__main__":
    parser = HfArgumentParser((ListenAndPlayArguments,))
    (args,) = parser.parse_args_into_dataclasses()
    listen_and_play(**vars(args))
