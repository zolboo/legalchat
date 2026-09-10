"""LegalChat — хажуудаа чаттай эрх зүйн лавлах вэб.

Чат хэсэг нь `local-llm-assistant` дахь **локал агентаар** ажиллана
(BM25 + bge-m3 hybrid эрэл → gemma3:12b эшлэлтэй хариулт), зүүн талын
дэлгэрэнгүй самбарт олдсон материал, заалтын бүтэн эх бичвэр харагдана.

Ажиллуулах (агентын виртуал орчноор — numpy, requests хэрэгтэй):

    /home/bllgvvn/local-llm-assistant/.venv/bin/python app.py

    python app.py --port 9000        # өөр порт
    python app.py --sources-only     # LLM ажиллуулахгүй, зөвхөн хайлт (шуурхай)
    python app.py --demo             # жишээ өгөгдөл (агентгүйгээр интерфейс үзэх)

Агент ачаалагдахгүй бол (индекс үүсээгүй, numpy байхгүй г.м) сервер
автоматаар жишээ өгөгдөл рүү шилжинэ.
"""

import argparse
import json
import mimetypes
import os
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
MAX_BODY = 64 * 1024

BACKEND = None  # main() дотор сонгогдоно


class LegalChatHandler(BaseHTTPRequestHandler):
    server_version = "LegalChat/2.0"
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

    def _read_json(self):
        """POST биеийг уншина. Алдаатай бол хариу буцаагаад None өгнө."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Хүсэлт хэт том байна.")
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON биш өгөгдөл.")
            return None

    # --- урсгал (Server-Sent Events) --------------------------------------
    #
    # Хариулт CPU дээр 3-4 минут үргэлжилдэг тул бүрэн болтол нь хүлээлгэхгүй,
    # эх сурвалжийг нэн даруй, текстийг токен тус бүрээр илгээнэ. HTTP/1.1
    # chunked encoding-оор дамжуулна (Content-Length урьдчилан мэдэгдэхгүй).

    def _open_stream(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")  # прокси ард ч буферлэхгүй
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write_chunk(self, data):
        self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")

    def _send_event(self, event, payload):
        body = "event: {}\ndata: {}\n\n".format(
            event, json.dumps(payload, ensure_ascii=False)
        ).encode("utf-8")
        self._write_chunk(body)

    def _close_stream(self):
        self.wfile.write(b"0\r\n\r\n")

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

        if path == "/api/status":
            self._send_json(BACKEND.status())
            return

        if path == "/api/suggestions":
            self._send_json({"suggestions": BACKEND.suggestions()})
            return

        if path.startswith("/api/doc/"):
            from urllib.parse import unquote

            doc = BACKEND.get_doc(unquote(path[len("/api/doc/"):]))
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
        path = self.path.split("?", 1)[0]

        if path == "/api/clear":
            BACKEND.reset()
            self._send_json({"ok": True})
            return

        if path in ("/api/chat", "/api/chat/stream"):
            payload = self._read_json()
            if payload is None:
                return
            question = (payload.get("question") or "").strip()
            if not question:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Асуултаа бичнэ үү.")
                return
            category = (payload.get("category") or "").strip() or None

            if path == "/api/chat":
                self._answer_once(question, category)
            else:
                self._answer_stream(question, category)
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Ийм API байхгүй.")

    # --- хариулт ---------------------------------------------------------

    def _answer_once(self, question, category):
        try:
            result = BACKEND.answer(question, category)
        except Exception as exc:  # noqa: BLE001 — Ollama унтрах г.м
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        result["question"] = question
        self._send_json(result)

    def _answer_stream(self, question, category):
        stream = BACKEND.stream_answer(question, category)
        self._open_stream()
        try:
            for event, data in stream:
                self._send_event(event, data)
            self._close_stream()
        except (BrokenPipeError, ConnectionResetError):
            # Хэрэглэгч хуудсаа хаасан эсвэл "Зогсоох" дарсан
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            try:
                self._send_event("error", {"error": str(exc)})
                self._close_stream()
            except OSError:
                self.close_connection = True
        finally:
            stream.close()

    def log_message(self, fmt, *args):
        print("  {} — {}".format(self.address_string(), fmt % args))


# ---------------------------------------------------------------- эхлүүлэх


def choose_backend(args):
    """Локал агентыг ачаална; болохгүй бол жишээ өгөгдөл рүү шилжинэ."""
    if not args.demo:
        import agentchat  # зөвхөн стандарт сан импортлодог тул энэ нь бүтнэ

        try:
            kwargs = {"sources_only": args.sources_only}
            if args.index:
                kwargs["index_dir"] = args.index
            if args.model:
                kwargs["chat_model"] = args.model
            if args.top_k:
                kwargs["top_k"] = args.top_k
            backend = agentchat.load_backend(**kwargs)
            info = backend.status()
            print("Агент ачааллаа: {} хэсэг / {} акт — {}".format(
                info["chunks"], info["documents"], info["index_dir"]))
            if info["llm"]:
                print("Хариулт үүсгэх модел: {} ({})".format(
                    info["chat_model"], info["host"]))
            else:
                print("LLM идэвхгүй — зөвхөн хайлт. {}".format(info["note"]))
            return backend
        except agentchat.AgentUnavailable as exc:
            print("Агентыг ачаалж чадсангүй:\n{}\n".format(exc), file=sys.stderr)
            print("Жишээ өгөгдлөөр үргэлжлүүлж байна (--demo).\n", file=sys.stderr)

    from search import DemoBackend

    print("Жишээ өгөгдлийн горим — агент холбогдоогүй.")
    return DemoBackend()


def main():
    parser = argparse.ArgumentParser(description="LegalChat сервер")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Хөтчийг автоматаар нээхгүй")
    parser.add_argument("--demo", action="store_true", help="Агентын оронд жишээ өгөгдөл")
    parser.add_argument("--sources-only", action="store_true",
                        help="LLM ажиллуулахгүй, зөвхөн олдсон материалыг харуулах")
    parser.add_argument("--model", default=None, help="хариулт үүсгэх модел (жнь gemma3:4b)")
    parser.add_argument("--index", default=None, help="индексийн хавтас")
    parser.add_argument("--top-k", type=int, default=None, help="контекст болгох хэсгийн тоо")
    args = parser.parse_args()

    # Лог руу дамжуулсан үед ч мессеж шууд харагдана
    sys.stdout.reconfigure(line_buffering=True)

    global BACKEND
    BACKEND = choose_backend(args)

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
