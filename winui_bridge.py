"""JSON-lines bridge between WinUI and the Python automation backend."""
from __future__ import annotations

import json
import sys
import threading

from core.winui_service import WinUIBackend

_write_lock = threading.Lock()
_protocol_out = sys.stdout


def write_message(message):
    with _write_lock:
        _protocol_out.write(json.dumps(message, separators=(",", ":")) + "\n")
        _protocol_out.flush()


def run_daemon():
    sys.stdout = sys.stderr
    backend = WinUIBackend(lambda event: write_message({"event": event}))
    write_message({"ready": True})
    for line in sys.stdin:
        try:
            request = json.loads(line or "{}")
            request_id = request.get("id")
            command = request.get("command")
            payload = request.get("payload") or {}
            result = backend.handle(command, payload)
            write_message({"id": request_id, "result": result})
            if command == "shutdown":
                break
        except Exception as exc:
            write_message({"id": request.get("id") if "request" in locals() else None, "error": str(exc)})


def run_oneshot():
    sys.stdout = sys.stderr
    backend = WinUIBackend(lambda _event: None)
    request = json.loads(sys.stdin.readline() or "{}")
    result = backend.handle(request.get("command"), request.get("payload") or {})
    write_message(result)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        run_oneshot()
