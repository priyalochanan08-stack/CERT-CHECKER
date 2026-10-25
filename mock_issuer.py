"""MOCK ISSUER with demo data (stdlib only). Not a real certificate authority."""
import html
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

RECORDS = {
    "GOOD1001": ("Jane Doe", "Machine Learning", "2024-03-15"),
    "GOOD1002": ("John Smith", "Data Analysis", "2024-05-20"),
    "TRAP2002": ("Alex Roe", "Cloud Basics", "2023-11-02"),  # shows a green VERIFIED badge, but a different name
}
STYLE = ("body{font-family:sans-serif;max-width:560px;margin:30px auto}.banner{background:#fde68a;padding:8px;"
         "text-align:center;font-weight:bold}.badge{display:inline-block;padding:6px 12px;border-radius:6px;"
         "color:#fff;font-weight:bold}.ok{background:#16a34a}.no{background:#dc2626}")


def render(cid):
    c = html.escape(cid)
    banner = '<div class="banner">MOCK ISSUER (demo data)</div>'
    if cid == "BLOCK001":
        body = "<h1>Please verify you are human</h1><p>CAPTCHA required to continue.</p>"
    elif cid in RECORDS:
        n, co, d = RECORDS[cid]
        body = (f'<h1>Certificate of Completion</h1><span class="badge ok">VERIFIED CERTIFICATE</span>'
                f"<p><b>Certificate ID:</b> {c}</p><p><b>Name:</b> {html.escape(n)}</p>"
                f"<p><b>Course:</b> {html.escape(co)}</p><p><b>Date:</b> {d}</p>")
    else:
        body = (f'<h1>Certificate lookup</h1><span class="badge no">RECORD NOT FOUND</span>'
                f"<p><b>Certificate ID:</b> {c}</p><p>No record found for this certificate ID.</p>")
    return f"<!doctype html><html><head><meta charset=utf-8><style>{STYLE}</style></head><body>{banner}{body}</body></html>"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.fullmatch(r"/verify/([A-Za-z0-9_\-]{1,64})", self.path.split("?")[0])
        code, body = (200, render(m.group(1))) if m else (404, "<h1>404</h1>")
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_background(port=None):
    """Starts the mock in a thread. Returns the server, or None if the port is already in use."""
    port = port or config.MOCK_PORT
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return None
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    print(f"Mock issuer (demo data) on http://localhost:{config.MOCK_PORT}/verify/GOOD1001")
    ThreadingHTTPServer(("127.0.0.1", config.MOCK_PORT), Handler).serve_forever()
