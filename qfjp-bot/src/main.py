"""
main.py — FastAPI app: healthcheck Railway + Scanner QF×JP
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import asyncio
import logging
import sys
import os

# ── Healthcheck HTTP server PRIMERO (antes de imports pesados) ──────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def _start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

# Arrancar healthcheck antes de cualquier otra cosa
_health_port = int(os.getenv("PORT", "8080"))
_start_health_server(_health_port)

# ── Ahora los imports pesados ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers= [logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")

from fastapi import FastAPI
import uvicorn

import config as cfg
from scanner import Scanner

app     = FastAPI(title="QF×JP Bot v3.5")
scanner = Scanner()


@app.get("/health")
async def health():
    return {"status": "ok", "mode": cfg.MODE}


@app.get("/status")
async def status():
    summary = scanner.risk.daily_summary()
    return {
        "mode":       cfg.MODE,
        "min_tier":   cfg.MIN_TIER,
        "scan_interval": cfg.SCAN_INTERVAL,
        **summary,
    }


@app.on_event("startup")
async def startup():
    asyncio.create_task(scanner.start())
    log.info(f"QF×JP Bot v3.5 PREDATOR — modo {cfg.MODE} — puerto {_health_port}")


@app.on_event("shutdown")
async def shutdown():
    await scanner.stop()


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = _health_port,
        workers = 1,
        log_level = "info",
    )
