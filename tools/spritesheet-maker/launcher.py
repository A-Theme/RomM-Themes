"""Desktop entry point: start the local server and open the page.

This is what the packaged build runs. Double-clicking an exe should end up at a
working page with no terminal work, so it picks its own free port, opens the
browser at it, and keeps a console window around as the way to read the URL
again and to stop the server.
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"          # local only, as the server has no auth and none is wanted
PREFERRED_PORTS = (8753, 8754, 8755, 8000, 8080)


def free_port() -> int:
    """First preferred port that is free, else whatever the OS hands out."""
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def open_when_ready(url: str, port: int, timeout: float = 25.0) -> None:
    """Wait for the port to answer, then open a browser at it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((HOST, port)) == 0:
                break
        time.sleep(0.2)
    try:
        webbrowser.open(url)
    except Exception:            # a headless or locked-down desktop: the URL is printed anyway
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprite Sheet Maker")
    parser.add_argument("--port", type=int, default=None, help="port to serve on")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    import uvicorn

    from app.main import TMP_DIR, ffmpeg_path

    port = args.port or free_port()
    url = f"http://{HOST}:{port}/"

    print("Sprite Sheet Maker")
    print(f"  serving   {url}")
    print(f"  job files {TMP_DIR}")
    print(f"  ffmpeg    {ffmpeg_path() or 'not found - video input and WebM export are off'}")
    print("  press Ctrl+C to stop")

    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()

    try:
        uvicorn.run("app.main:app", host=HOST, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
