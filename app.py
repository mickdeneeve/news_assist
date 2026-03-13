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
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_PORT = "8000"
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "PORT")


def parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None

    key, value = line.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    value = value.strip()

    if not key:
        return None

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return key, value


def read_env_file(path: Path) -> dict[str, str]:
    settings: dict[str, str] = {}
    if not path.exists():
        return settings

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue

        key, value = parsed
        settings[key] = value

    return settings


def load_dotenv(path: Path) -> None:
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def format_env_value(value: str) -> str:
    if any(char in value for char in ('"', "'", "#", " ", "\t")):
        return json.dumps(value)
    return value


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    settings = read_env_file(path)
    settings.update(updates)
    settings.setdefault("OPENAI_MODEL", DEFAULT_MODEL)
    settings.setdefault("PORT", DEFAULT_PORT)

    lines = ["# Local app configuration"]
    written_keys: set[str] = set()

    for key in CONFIG_KEYS:
        if key in settings:
            lines.append(f"{key}={format_env_value(settings[key])}")
            written_keys.add(key)

    for key, value in settings.items():
        if key in written_keys:
            continue
        lines.append(f"{key}={format_env_value(value)}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def current_model() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def current_port() -> str:
    return os.environ.get("PORT", DEFAULT_PORT).strip() or DEFAULT_PORT


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 4:
        return "****"
    return f"****{secret[-4:]}"


load_dotenv(ENV_FILE)


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
                    "model": current_model(),
                },
            )
            return
        if route == "/api/config":
            self._send_json(200, self._config_payload())
            return

        if route == "/":
            self.path = "/index.html"
        elif route == "/config":
            self.path = "/config.html"
        else:
            self.path = route
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/chat":
            self._handle_chat()
            return
        if route == "/api/config":
            self._handle_config_update()
            return

        self._send_json(404, {"error": "Route not found."})

    def _handle_chat(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        query = str(payload.get("query", "")).strip()
        if not query:
            self._send_json(400, {"error": "Please provide a non-empty query."})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": "OPENAI_API_KEY is not set on the server."})
            return

        model = current_model()
        request_body = json.dumps({"model": model, "input": query}).encode("utf-8")
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

        self._send_json(200, {"model": model, "response": answer})

    def _handle_config_update(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        model = str(payload.get("openai_model", current_model())).strip()
        if not model:
            self._send_json(400, {"error": "OPENAI_MODEL cannot be empty."})
            return

        port = str(payload.get("port", current_port())).strip()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            self._send_json(400, {"error": "PORT must be a number between 1 and 65535."})
            return

        updates = {"OPENAI_MODEL": model, "PORT": port}
        clear_api_key = bool(payload.get("clear_api_key"))
        new_api_key = str(payload.get("openai_api_key", "")).strip()

        if clear_api_key:
            updates["OPENAI_API_KEY"] = ""
        elif new_api_key:
            updates["OPENAI_API_KEY"] = new_api_key

        write_env_file(ENV_FILE, updates)
        for key, value in updates.items():
            os.environ[key] = value

        server_port = str(self.server.server_address[1])
        restart_required = port != server_port
        message = "Configuration saved."
        if restart_required:
            message += f" Restart the server to switch from port {server_port} to {port}."

        response_payload = self._config_payload()
        response_payload["message"] = message
        response_payload["restart_required"] = restart_required
        self._send_json(200, response_payload)

    def _read_json_body(self) -> dict[str, object] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length header."})
            return None

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return None

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Request body must be a JSON object."})
            return None

        return payload

    def _config_payload(self) -> dict[str, object]:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        configured_port = current_port()
        server_port = str(self.server.server_address[1])

        return {
            "openai_api_key_hint": mask_secret(api_key),
            "openai_api_key_set": bool(api_key),
            "openai_model": current_model(),
            "configured_port": configured_port,
            "server_port": server_port,
        }

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
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
