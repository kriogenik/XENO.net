#!/usr/bin/env python3
"""Serve Happ subscription as text/plain on :2080 (avoid confusion with panels)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/var/www/xeno-sub")
# single token directory
TOKEN = next(p.name for p in ROOT.iterdir() if p.is_dir())
BODY = (ROOT / TOKEN / "sub.txt").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") + "/"
        if path in (f"/{TOKEN}/", f"/sub/{TOKEN}/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(BODY)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(BODY)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 2080), Handler).serve_forever()
