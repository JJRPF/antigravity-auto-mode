#!/usr/bin/env python3
"""
laya_daemon.py: Fast, On-Demand Local Decision Engine Daemon
Powered by Laya (convaiinnovations/laya-typed-decisions).

Features:
- Sub-40ms local System 1 decisions.
- Identical schema to TypeSafe Jev (choice, score, noul).
- Auto-terminates after 10 minutes of inactivity (zero idle RAM).
- Terminates immediately on system suspend/lid-close via systemd D-Bus (zero sleep battery drain).
"""

import os
import sys
import time
import json
import socket
import logging
import threading
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

HOST = "127.0.0.1"
PORT = 8765
IDLE_TIMEOUT_SECONDS = 600  # 10 minutes

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [laya-daemon] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("laya_daemon")

# Global state
last_active_time = time.time()
model_instance = None
server_instance: Optional[HTTPServer] = None


def load_laya_model():
    """Load the specialized typed-decisions checkpoint."""
    global model_instance
    log.info("Loading Laya model: convaiinnovations/laya (subfolder=typed-decisions)...")
    t0 = time.time()
    try:
        import laya
        # Check local snapshot cache first to avoid network round-trips
        cached_snapshots = os.path.expanduser("~/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots")
        local_dir = None
        if os.path.isdir(cached_snapshots):
            dirs = [os.path.join(cached_snapshots, d, "typed-decisions") for d in os.listdir(cached_snapshots)]
            valid = [d for d in dirs if os.path.isdir(d) and os.path.isfile(os.path.join(d, "model.safetensors"))]
            if valid:
                local_dir = valid[0]

        if local_dir:
            log.info(f"Loading directly from local snapshot: {local_dir}")
            model_instance = laya.load(local_dir)
        else:
            model_instance = laya.load("convaiinnovations/laya", subfolder="typed-decisions")
        log.info(f"Loaded specialized typed-decisions in {time.time() - t0:.2f}s")
    except Exception as e:
        log.error(f"Fatal: Failed to load Laya model: {e}")
        sys.exit(1)


def touch_activity():
    global last_active_time
    last_active_time = time.time()


class LayaRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress routine request logging to keep console clean
        pass

    def do_GET(self):
        touch_activity()
        if self.path == "/health":
            idle_sec = time.time() - last_active_time
            res = {
                "status": "ok",
                "engine": "laya",
                "model": "convaiinnovations/laya-typed-decisions",
                "idle_seconds": round(idle_sec, 1),
                "idle_timeout": IDLE_TIMEOUT_SECONDS,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        touch_activity()
        if self.path in ("/predict", "/v1/systemone"):
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                data = json.loads(body)

                state = data.get("state", {})
                questions = data.get("questions", {})

                if not questions:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Missing questions"}')
                    return

                t0 = time.time()
                # Run prediction
                if hasattr(model_instance, "predict"):
                    prediction = model_instance.predict(state, questions)
                else:
                    # Direct callable
                    prediction = model_instance(state, questions)
                dt_ms = (time.time() - t0) * 1000

                # Normalize response format to match Jev answers structure
                answers = prediction.get("answers", prediction)
                res = {
                    "answers": answers,
                    "engine": "laya",
                    "latency_ms": round(dt_ms, 2),
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                log.error(f"Error during predict: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif self.path == "/shutdown":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "shutting down"}')
            log.info("Shutdown endpoint requested.")
            threading.Thread(target=graceful_shutdown).start()
        else:
            self.send_response(404)
            self.end_headers()


def graceful_shutdown(reason: str = "Requested"):
    global server_instance
    log.info(f"Shutting down Laya daemon: {reason}...")
    if server_instance:
        server_instance.shutdown()
    sys.exit(0)


def inactivity_watchdog():
    """Checks idle time and self-terminates after 10 minutes of inactivity."""
    while True:
        time.sleep(10)
        idle = time.time() - last_active_time
        if idle >= IDLE_TIMEOUT_SECONDS:
            log.info(f"Idle timeout reached ({idle:.0f}s >= {IDLE_TIMEOUT_SECONDS}s). Unloading model.")
            graceful_shutdown(reason="Inactivity timeout")
            break


def sleep_monitor():
    """Listens for systemd PrepareForSleep signal to shut down immediately before lid close/suspend."""
    log.info("Starting D-Bus sleep monitor via busctl...")
    try:
        proc = subprocess.Popen(
            ["busctl", "monitor", "--system", "org.freedesktop.login1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if proc.stdout:
            for line in proc.stdout:
                if "PrepareForSleep" in line and "true" in line.lower():
                    log.info("System preparing for sleep (lid close / suspend). Terminating daemon.")
                    graceful_shutdown(reason="System sleep / lid closed")
                    break
    except Exception as e:
        log.warning(f"Could not initialize busctl sleep monitor ({e}). Relying on wall-clock watchdog.")


def main():
    global server_instance
    # Ensure port is available
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, PORT))
        sock.close()
    except OSError:
        log.error(f"Port {PORT} is already in use. Is another laya_daemon running?")
        sys.exit(1)

    load_laya_model()

    server_instance = HTTPServer((HOST, PORT), LayaRequestHandler)
    log.info(f"Laya Daemon active and listening on http://{HOST}:{PORT}")

    # Launch background watchdogs
    threading.Thread(target=inactivity_watchdog, daemon=True).start()
    threading.Thread(target=sleep_monitor, daemon=True).start()

    try:
        server_instance.serve_forever()
    except KeyboardInterrupt:
        graceful_shutdown(reason="Interrupted")


if __name__ == "__main__":
    main()
