from __future__ import annotations

import socket


def is_port_free(bind: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((bind, port))
            return True
    except OSError:
        return False


def find_free_port(bind: str = "127.0.0.1", preferred: int = 0) -> int:
    if preferred and is_port_free(bind, preferred):
        return preferred

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind, 0))
        return int(sock.getsockname()[1])
