#!/usr/bin/env python3
"""
PDF → OFX Converter — desktop launcher.

Boots the Flask app on a free localhost port, opens the user's default
browser to the app, and keeps the console window open so the user can
close it (or hit Ctrl+C) to shut everything down.
"""

import logging
import os
import socket
import sys
import threading
import webbrowser

from app import app, APP_VERSION


# ─────────────────────────────────────────────
#  PyInstaller compatibility
# ─────────────────────────────────────────────
# When frozen by PyInstaller, bundled data files live under sys._MEIPASS,
# not next to app.py. Re-point Flask's template loader so render_template
# still works inside the .exe.
if getattr(sys, "frozen", False):
    from jinja2 import FileSystemLoader

    base = sys._MEIPASS  # type: ignore[attr-defined]
    app.template_folder = os.path.join(base, "templates")
    app.jinja_loader = FileSystemLoader(app.template_folder)
    static_dir = os.path.join(base, "static")
    if os.path.isdir(static_dir):
        app.static_folder = static_dir


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def find_free_port() -> int:
    """Return an OS-assigned free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def print_banner(url: str) -> None:
    line = "=" * 56
    print()
    print(f"  {line}")
    print(f"   PDF -> OFX Converter   v{APP_VERSION}")
    print(f"  {line}")
    print(f"   Opening in your browser:  {url}")
    print(f"   Close this window (or press Ctrl+C) to shut down.")
    print(f"  {line}")
    print()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print_banner(url)

    # Open the browser shortly after Flask starts accepting connections.
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # Silence Werkzeug's per-request log so the console stays readable.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # Block here until the user closes the console window or hits Ctrl+C.
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Shutting down. Goodbye.")
        sys.exit(0)
