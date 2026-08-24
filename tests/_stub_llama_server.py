"""Stub llama-server for SidecarManager tests.

Speaks just enough HTTP: GET /health -> 200. Binds the port passed via
--port <N> in argv (the manager picks it); ignores all other llama.cpp flags.
"""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _arg_port(default=0):
    a = sys.argv
    for i, v in enumerate(a):
        if v == "--port" and i + 1 < len(a):
            return int(a[i + 1])
    return default


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", _arg_port()), H)
sys.stderr.write(f"stub-ready {srv.server_address[1]}\n")
sys.stderr.flush()
srv.serve_forever()
