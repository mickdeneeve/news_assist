from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).parent
STATIC_DIR = ROOT_DIR / "static"
ENV_FILE = ROOT_DIR / ".env"
OPENAI_API_URL = "https://api.openai.com/v1/responses"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


load_dotenv(ENV_FILE)
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        route = urlparse(self.path).path

        if route == "/api/hello":
            self._send_json(
                200,
                {
                    "message": "Use POST /api/chat with a query to talk to the model.",
                    "model": DEFAULT_MODEL,
                },
            )
            return

        self.path = "/index.html" if route == "/" else route
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route != "/api/chat":
            self._send_json(404, {"error": "Route not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length header."})
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return

        query = ""
        if isinstance(payload, dict):
            query = str(payload.get("query", "")).strip()

        if not query:
            self._send_json(400, {"error": "Please provide a non-empty query."})
            return

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._send_json(500, {"error": "OPENAI_API_KEY is not set on the server."})
            return

        request_body = json.dumps({"model": DEFAULT_MODEL, "input": query}).encode("utf-8")
        request = Request(
            OPENAI_API_URL,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=60) as response:
                upstream_payload = json.loads(response.read())
        except HTTPError as error:
            message = self._extract_error_message(error.read())
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(502, {"error": f"Could not reach OpenAI: {error.reason}"})
            return

        answer = self._extract_output_text(upstream_payload)
        if not answer:
            self._send_json(502, {"error": "OpenAI returned no text output."})
            return

        self._send_json(200, {"model": DEFAULT_MODEL, "response": answer})

    def _send_json(self, status_code: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _extract_error_message(self, raw_error: bytes) -> str:
        try:
            payload = json.loads(raw_error)
        except json.JSONDecodeError:
            return raw_error.decode("utf-8", errors="replace") or "OpenAI request failed."

        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message", "OpenAI request failed."))
        return "OpenAI request failed."

    def _extract_output_text(self, payload: dict[str, object]) -> str:
        pieces: list[str] = []

        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue

            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text", "")
                    if text:
                        pieces.append(str(text))

        return "\n\n".join(pieces).strip()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Serving on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
