"""
Native Messaging Host — starts the uvicorn server on demand.

Chrome protocol: 4-byte LE uint32 length prefix + UTF-8 JSON on stdin/stdout.
Messages: {"type": "start-server"} → {"ok": true} | {"ok": false, "error": "..."}
"""
import json
import os
import pathlib
import struct
import subprocess
import sys
import time
import urllib.request


PORT = 8765
ROOT = pathlib.Path(__file__).parent.parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _read_message() -> dict:
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        sys.exit(0)
    length = struct.unpack("<I", raw_len)[0]
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _send_message(msg: dict) -> None:
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _server_running() -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=2)
        return True
    except Exception:
        return False


def _start_server() -> None:
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    log_path = ROOT / "server.log"
    log_file = open(log_path, "a", encoding="utf-8")
    subprocess.Popen(
        [python, "-m", "uvicorn", "server.main:app", "--port", str(PORT),
         "--log-level", "info"],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=log_file,
        stderr=log_file,
    )
    # Wait up to 30s for server to be ready
    for _ in range(30):
        time.sleep(1)
        if _server_running():
            return
    raise RuntimeError("Server did not start within 30s")


def main():
    msg = _read_message()
    if msg.get("type") == "start-server":
        try:
            if not _server_running():
                _start_server()
            _send_message({"ok": True})
        except Exception as e:
            _send_message({"ok": False, "error": str(e)})
    else:
        _send_message({"ok": False, "error": f"Unknown message type: {msg.get('type')}"})


if __name__ == "__main__":
    main()
