from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).parent
STATIC_DIR = ROOT_DIR / "static"
ENV_FILE = ROOT_DIR / ".env"
REPORTING_CONFIG_FILE = ROOT_DIR / "journalism_config.json"
OPENAI_RESPONSES_API_URL = "https://api.openai.com/v1/responses"
OPENAI_MODELS_API_URL = "https://api.openai.com/v1/models"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_PORT = "8000"
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "PORT")
RELOAD_ENV_VAR = "NEWS_ASSIST_RUN_SERVER"
DISABLE_RELOAD_ENV_VAR = "NEWS_ASSIST_DISABLE_RELOAD"
WATCHED_SUFFIXES = {".py"}
IGNORED_DIRS = {".git", "__pycache__"}
DEFAULT_REPORTING_QUESTIONS = [
    "What happened, in one clear summary?",
    "Who are the key people, organizations, or institutions involved?",
    "Why does this matter right now?",
    "What context or background does a reader need?",
    "What remains unknown, disputed, or unverified?",
]


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


def default_reporting_config() -> dict[str, list[str]]:
    return {"questions": DEFAULT_REPORTING_QUESTIONS.copy()}


def normalize_questions(raw_questions: object) -> list[str]:
    normalized: list[str] = []
    if not isinstance(raw_questions, list):
        return normalized

    for raw_question in raw_questions:
        question = str(raw_question).strip()
        if question:
            normalized.append(question)

    return normalized


def read_reporting_config(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return default_reporting_config()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_reporting_config()

    if not isinstance(payload, dict):
        return default_reporting_config()

    questions = normalize_questions(payload.get("questions"))
    if not questions:
        questions = DEFAULT_REPORTING_QUESTIONS.copy()

    return {"questions": questions}


def write_reporting_config(path: Path, questions: list[str]) -> None:
    payload = {"questions": questions}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ensure_local_reporting_config() -> None:
    if REPORTING_CONFIG_FILE.exists():
        return

    write_reporting_config(REPORTING_CONFIG_FILE, DEFAULT_REPORTING_QUESTIONS.copy())


def extract_openai_error_message(raw_error: bytes) -> str:
    try:
        payload = json.loads(raw_error)
    except json.JSONDecodeError:
        return raw_error.decode("utf-8", errors="replace") or "OpenAI request failed."

    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message", "OpenAI request failed."))
    return "OpenAI request failed."


def extract_output_text(payload: dict[str, object]) -> str:
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


def fetch_available_models() -> tuple[list[str], str | None]:
    current = current_model()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return [current], "OPENAI_API_KEY is not configured on the server."

    request = Request(
        OPENAI_MODELS_API_URL,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return [current], extract_openai_error_message(error.read())
    except URLError as error:
        return [current], f"Could not reach OpenAI: {error.reason}"

    data = payload.get("data", [])
    models = sorted(
        {
            str(item.get("id", "")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        },
        key=str.lower,
    )

    if current not in models:
        models.insert(0, current)

    return models, None


def build_briefing_prompt(topic: str, questions: list[str]) -> str:
    numbered_questions = "\n".join(
        f"{index}. {question}" for index, question in enumerate(questions, start=1)
    )

    return (
        "You are a journalism research assistant. Answer each reporting question about the provided topic or event. "
        "Write concise, factual briefings for a reporter. Be explicit about uncertainty, distinguish what is known "
        "from what remains unclear, and do not invent sources, quotes, or details. Return valid JSON only, with "
        'this exact shape: {"answers":[{"question":"<copy the original question>","answer":"<answer>"}]}. '
        "Keep the questions in the same order and include every question exactly once."
        "\n\n"
        f"Topic or event:\n{topic}\n\n"
        f"Reporting questions:\n{numbered_questions}\n"
    )


def parse_briefing_output(text: str, questions: list[str]) -> list[dict[str, str]]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = "\n".join(
            line for line in candidate.splitlines() if not line.strip().startswith("```")
        ).strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]

    payload = json.loads(candidate)
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise ValueError("OpenAI returned an unexpected answers payload.")

    normalized_answers: list[dict[str, str]] = []
    for question, item in zip(questions, answers):
        if not isinstance(item, dict):
            raise ValueError("OpenAI returned a malformed answer entry.")

        answer = str(item.get("answer", "")).strip()
        if not answer:
            raise ValueError("OpenAI returned an empty answer.")

        normalized_answers.append({"question": question, "answer": answer})

    return normalized_answers


def iter_watched_files() -> list[Path]:
    watched_files: list[Path] = []

    for path in ROOT_DIR.rglob("*"):
        if not path.is_file():
            continue

        relative_parts = path.relative_to(ROOT_DIR).parts
        if any(part in IGNORED_DIRS for part in relative_parts):
            continue

        if path.suffix in WATCHED_SUFFIXES:
            watched_files.append(path)

    watched_files.sort()
    return watched_files


def take_watch_snapshot() -> dict[str, int]:
    return {
        str(path.relative_to(ROOT_DIR)): path.stat().st_mtime_ns
        for path in iter_watched_files()
    }


def start_server_process() -> subprocess.Popen:
    env = os.environ.copy()
    env[RELOAD_ENV_VAR] = "1"

    return subprocess.Popen([sys.executable, str(ROOT_DIR / "app.py")], cwd=str(ROOT_DIR), env=env)


def stop_server_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_with_reloader() -> None:
    snapshot = take_watch_snapshot()
    process = start_server_process()
    waiting_for_change = False
    print(f"Watching {ROOT_DIR} for Python changes. Set {DISABLE_RELOAD_ENV_VAR}=1 to disable auto-reload.", flush=True)

    try:
        while True:
            time.sleep(1)

            if process.poll() is not None and not waiting_for_change:
                print(
                    f"Server exited with code {process.returncode}. Waiting for a Python file change before restart.",
                    flush=True,
                )
                waiting_for_change = True

            updated_snapshot = take_watch_snapshot()
            if updated_snapshot == snapshot:
                continue

            snapshot = updated_snapshot
            print("Python change detected. Restarting server...", flush=True)

            if process.poll() is None:
                stop_server_process(process)

            process = start_server_process()
            waiting_for_change = False
    except KeyboardInterrupt:
        stop_server_process(process)
        print("\nStopped auto-reloading server.", flush=True)


load_dotenv(ENV_FILE)
ensure_local_reporting_config()


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_HEAD(self) -> None:
        self._map_static_route()
        super().do_HEAD()

    def do_GET(self) -> None:
        route = urlparse(self.path).path

        if route == "/api/hello":
            self._send_json(
                200,
                {
                    "message": "Use the main page to brief a topic or event against your configured reporting questions.",
                    "model": current_model(),
                },
            )
            return

        if route == "/api/config":
            self._send_json(200, self._config_payload())
            return

        self._map_static_route()
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path

        if route == "/api/chat":
            self._handle_briefing_request()
            return

        if route == "/api/config":
            self._handle_config_update()
            return

        self._send_json(404, {"error": "Route not found."})

    def _handle_briefing_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        topic = str(payload.get("query", "")).strip()
        if not topic:
            self._send_json(400, {"error": "Please provide a topic or event to investigate."})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": "OPENAI_API_KEY is not set on the server."})
            return

        model = current_model()
        reporting_questions = read_reporting_config(REPORTING_CONFIG_FILE)["questions"]
        request_body = json.dumps(
            {"model": model, "input": build_briefing_prompt(topic, reporting_questions)}
        ).encode("utf-8")
        request = Request(
            OPENAI_RESPONSES_API_URL,
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
            message = extract_openai_error_message(error.read())
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(502, {"error": f"Could not reach OpenAI: {error.reason}"})
            return

        raw_output = extract_output_text(upstream_payload)
        if not raw_output:
            self._send_json(502, {"error": "OpenAI returned no text output."})
            return

        try:
            answers = parse_briefing_output(raw_output, reporting_questions)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(502, {"error": f"OpenAI returned an invalid briefing payload: {error}"})
            return

        self._send_json(
            200,
            {
                "model": model,
                "topic": topic,
                "answers": answers,
            },
        )

    def _handle_config_update(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        model = str(payload.get("openai_model", current_model())).strip()
        if not model:
            self._send_json(400, {"error": "OPENAI_MODEL cannot be empty."})
            return

        questions = normalize_questions(payload.get("questions"))
        if not questions:
            self._send_json(400, {"error": "Add at least one reporting question."})
            return

        write_env_file(ENV_FILE, {"OPENAI_MODEL": model})
        os.environ["OPENAI_MODEL"] = model
        write_reporting_config(REPORTING_CONFIG_FILE, questions)

        response_payload = self._config_payload()
        response_payload["message"] = "Configuration saved."
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
        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        available_models, models_error = fetch_available_models()

        return {
            "openai_model": current_model(),
            "available_models": available_models,
            "questions": reporting_config["questions"],
            "models_error": models_error,
        }

    def _map_static_route(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self.path = "/index.html"
        elif route == "/config":
            self.path = "/config.html"
        else:
            self.path = route

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve() -> None:
    port = int(current_port())
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Serving on http://127.0.0.1:{port}")
    server.serve_forever()


def main() -> None:
    if os.environ.get(RELOAD_ENV_VAR) == "1" or os.environ.get(DISABLE_RELOAD_ENV_VAR) == "1":
        serve()
        return

    run_with_reloader()


if __name__ == "__main__":
    main()
