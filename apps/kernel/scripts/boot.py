"""Dual-stack uvicorn launcher for the kernel API.

Why this exists: on Fly's Linux machines, `uvicorn --host ::` listens on
IPv6 only — IPV6_V6ONLY is effectively on, so IPv4 connections to the same
port refuse. `uvicorn --host 0.0.0.0` is the inverse.

We need both:
  - Fly's `<app>.internal` DNS returns IPv6 (6PN). Other Fly apps connect
    that way → must accept IPv6.
  - Fly Proxy → machine (public edge traffic) connects over IPv4 inside
    the machine → must accept IPv4.

Solution: open one AF_INET and one AF_INET6 socket explicitly (V6ONLY=1
on the v6 side so the two don't fight over the port), and hand both to
uvicorn via `Server.serve(sockets=[...])`.
"""

from __future__ import annotations

import os
import socket
import sys

import uvicorn


def _listen(family: int, addr: tuple, *, v6only: bool = False) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if v6only:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind(addr)
    sock.listen(128)
    sock.setblocking(False)
    return sock


def main() -> int:
    port = int(os.environ.get("PORT", "8000"))
    app = os.environ.get("OWNEVO_APP_TARGET", "ownevo_kernel.api.app:app")

    sock_v4 = _listen(socket.AF_INET, ("0.0.0.0", port))
    sock_v6 = _listen(socket.AF_INET6, ("::", port, 0, 0), v6only=True)

    config = uvicorn.Config(app, workers=1, log_level="info")
    server = uvicorn.Server(config)

    print(f"boot.py: serving {app} on 0.0.0.0:{port} and [::]:{port}", flush=True)
    server.run(sockets=[sock_v4, sock_v6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
