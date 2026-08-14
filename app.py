"""LegalChat — хажуудаа чаттай эрх зүйн лавлах вэб.

Зөвхөн Python-ы стандарт сан ашигласан тул pip install шаардлагагүй.

Ажиллуулах:
    python app.py            # http://127.0.0.1:8000
    python app.py --port 9000
"""

import argparse
import json
import mimetypes
import os
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from data import SUGGESTIONS
from search import answer, get_doc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
MAX_BODY = 64 * 1024


class LegalChatHandler(BaseHTTPRequestHandler):
    server_version = "LegalChat/1.0"
    protocol_version = "HTTP/1.1"

    # --- туслах методууд -------------------------------------------------

    def _send(self, status, body, content_type, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False)
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status=status)

    def _serve_static(self, rel_path):
        rel_path = rel_path.lstrip("/") or "index.html"
        full_path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        # Хавтаснаас гарах оролдлогоос хамгаалах
        if not full_path.startswith(STATIC_DIR) or not os.path.isfile(full_path):
            self._send(HTTPStatus.NOT_FOUND, "404 — олдсонгүй", "text/plain; charset=utf-8")
            return
        ctype, _ = mimetypes.guess_type(full_path)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full_path, "rb") as fh:
            self._send(HTTPStatus.OK, fh.read(), ctype, {"Cache-Control": "no-cache"})

    # --- маршрутууд ------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/suggestions":
            self._send_json({"suggestions": SUGGESTIONS})
            return

        if path.startswith("/api/doc/"):
            doc_id = path[len("/api/doc/"):]
            doc = get_doc(doc_id)
            if doc is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Ийм баримт олдсонгүй.")
                return
            self._send_json(doc)
            return

        if path.startswith("/api/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "Ийм API байхгүй.")
            return

        self._serve_static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/chat":
            self._send_error_json(HTTPStatus.NOT_FOUND, "Ийм API байхгүй.")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Хүсэлт хэт том байна.")
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON биш өгөгдөл.")
            return

        question = (payload.get("question") or "").strip()
        if not question:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Асуултаа бичнэ үү.")
            return

        result = answer(question)
        result["question"] = question
        self._send_json(result)

    def log_message(self, fmt, *args):
        print("  {} — {}".format(self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description="LegalChat демо сервер")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Хөтчийг автоматаар нээхгүй")
    args = parser.parse_args()

    url = "http://{}:{}/".format(args.host, args.port)
    server = ThreadingHTTPServer((args.host, args.port), LegalChatHandler)
    print("LegalChat ажиллаж байна: " + url)
    print("Зогсоох: Ctrl+C")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗогслоо.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
