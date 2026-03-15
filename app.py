from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).parent
STATIC_DIR = ROOT_DIR / "static"
ENV_FILE = ROOT_DIR / ".env"
REPORTING_CONFIG_FILE = ROOT_DIR / "journalism_config.json"
OPENAI_RESPONSES_API_URL = "https://api.openai.com/v1/responses"
OPENAI_MODELS_API_URL = "https://api.openai.com/v1/models"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_PORT = "8000"
SERVER_INSTANCE_ID = f"{os.getpid()}-{time.time_ns()}"
APP_USER_AGENT = "news-assist/1.0 (local journalism research tool)"
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "PORT")
RELOAD_ENV_VAR = "NEWS_ASSIST_RUN_SERVER"
DISABLE_RELOAD_ENV_VAR = "NEWS_ASSIST_DISABLE_RELOAD"
WATCHED_SUFFIXES = {".py"}
IGNORED_DIRS = {".git", "__pycache__"}
RESTART_EXIT_CODE = 75
DEFAULT_EDITION = "international_en"
EDITION_PROFILES = {
    "international_en": {
        "region": "international",
        "language": "en",
        "defaults": {
            "questions": [
                "What happened, in one clear summary?",
                "Who are the key people, organizations, or institutions involved?",
                "Why does this matter right now?",
                "What context or background does a reader need?",
                "What remains unknown, disputed, or unverified?",
            ],
            "article_query": (
                "Write a news article of around N words. It should have the most general information first, "
                "and the more specific stuff after."
            ),
        },
    },
    "netherlands_nl": {
        "region": "netherlands",
        "language": "nl",
        "defaults": {
            "questions": [
                "Wat is er gebeurd, in een heldere samenvatting?",
                "Wie zijn de belangrijkste personen, organisaties of instellingen die hierbij betrokken zijn?",
                "Waarom is dit nu van belang?",
                "Welke context of achtergrond heeft een lezer nodig?",
                "Wat is nog onbekend, omstreden of niet geverifieerd?",
            ],
            "article_query": (
                "Schrijf een nieuwsartikel van ongeveer N woorden. Zet de meest algemene informatie eerst "
                "en de meer specifieke informatie daarna."
            ),
        },
    },
}
SUPPORTED_LANGUAGES = {str(profile["language"]) for profile in EDITION_PROFILES.values()}
DEFAULT_ARTICLE_WORD_COUNT = 300
DEFAULT_ARTICLE_SELECTION_MODE = "exclude"
ARTICLE_SELECTION_MODES = {"include", "exclude"}
ARTICLE_WORD_PLACEHOLDER = "N"
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
WIKIPEDIA_LINK_CACHE: dict[tuple[str, str], str] = {}
SOURCE_TITLE_CACHE: dict[str, str] = {}
HTML_COMMENT_PATTERN = re.compile(r"<!--[\s\S]*?-->")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
HTML_ATTR_PATTERN = re.compile(
    r"([a-zA-Z_:][\w:.-]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)"
)
HTML_WHITESPACE_PATTERN = re.compile(r"\s+")
JSON_LD_SCRIPT_PATTERN = re.compile(
    r"<script\b[^>]*type\s*=\s*(?:\"application/ld\+json\"|'application/ld\+json'|application/ld\+json)[^>]*>"
    r"([\s\S]*?)</script>",
    re.IGNORECASE,
)
META_TAG_PATTERN = re.compile(r"<meta\b[^>]+>", re.IGNORECASE)
TITLE_TAG_PATTERN = re.compile(r"<title\b[^>]*>([\s\S]*?)</title>", re.IGNORECASE)
H1_TAG_PATTERN = re.compile(r"<h1\b[^>]*>([\s\S]*?)</h1>", re.IGNORECASE)
HEXISH_TITLE_PATTERN = re.compile(r"^[a-f0-9]{12,}$", re.IGNORECASE)
TITLE_SEPARATORS = (" | ", " - ", " — ", " – ", " :: ", " / ")
PRIVACY_GATE_PATTERNS = (
    "privacy gate",
    "cookie consent",
    "cookies",
    "privacy instellingen",
    "privacy instellingen beheren",
    "privacy settings",
    "consent",
    "toestemming",
    "manage your privacy",
    "manage privacy settings",
    "accept cookies",
    "dpg media privacy gate",
)
BACKEND_TEXT = {
    "en": {
        "openai_error_default": "OpenAI request failed.",
        "models_api_key_missing": "OPENAI_API_KEY is not configured on the server.",
        "could_not_reach_openai": "Could not reach OpenAI: {reason}",
        "hello_message": "Use the main page to brief a topic or event against your configured reporting questions.",
        "route_not_found": "Route not found.",
        "provide_topic": "Please provide a topic or event to investigate.",
        "api_key_missing": "OPENAI_API_KEY is not set on the server.",
        "no_text_output": "OpenAI returned no text output.",
        "invalid_briefing_payload": "OpenAI returned an invalid briefing payload: {error}",
        "invalid_translation_payload": "OpenAI returned an invalid translation payload: {error}",
        "no_excerpts": "No usable briefing text was provided for article drafting.",
        "no_snapshot_answers": "No briefing answers are available to translate.",
        "no_article_text": "OpenAI returned no article text.",
        "model_empty": "OPENAI_MODEL cannot be empty.",
        "add_question": "Add at least one reporting question.",
        "article_query_placeholder": "Article query must include the placeholder N for the configured word count.",
        "article_word_positive": "Article word count must be greater than zero.",
        "config_saved": "Configuration saved.",
        "restart_started": "App re-initialization started. Waiting for the server to restart.",
        "invalid_content_length": "Invalid Content-Length header.",
        "invalid_json": "Request body must be valid JSON.",
        "json_object_required": "Request body must be a JSON object.",
        "unexpected_answers_payload": "OpenAI returned an unexpected answers payload.",
        "malformed_answer_entry": "OpenAI returned a malformed answer entry.",
        "empty_answer": "OpenAI returned an empty answer.",
    },
    "nl": {
        "openai_error_default": "OpenAI-aanvraag mislukt.",
        "models_api_key_missing": "OPENAI_API_KEY is niet geconfigureerd op de server.",
        "could_not_reach_openai": "Kon OpenAI niet bereiken: {reason}",
        "hello_message": "Gebruik de hoofdpagina om een onderwerp of gebeurtenis te briefen aan de hand van je ingestelde verslaggeversvragen.",
        "route_not_found": "Route niet gevonden.",
        "provide_topic": "Geef eerst een onderwerp of gebeurtenis op.",
        "api_key_missing": "OPENAI_API_KEY is niet ingesteld op de server.",
        "no_text_output": "OpenAI gaf geen tekstuitvoer terug.",
        "invalid_briefing_payload": "OpenAI gaf een ongeldige briefingpayload terug: {error}",
        "invalid_translation_payload": "OpenAI gaf een ongeldige vertaalpayload terug: {error}",
        "no_excerpts": "Er is geen bruikbare briefingtekst opgegeven voor het opstellen van het artikel.",
        "no_snapshot_answers": "Er zijn geen briefingantwoorden beschikbaar om te vertalen.",
        "no_article_text": "OpenAI gaf geen artikeltekst terug.",
        "model_empty": "OPENAI_MODEL mag niet leeg zijn.",
        "add_question": "Voeg minstens een verslaggeversvraag toe.",
        "article_query_placeholder": "De artikelopdracht moet de placeholder N bevatten voor het ingestelde woordenaantal.",
        "article_word_positive": "Het woordenaantal voor het artikel moet groter zijn dan nul.",
        "config_saved": "Configuratie opgeslagen.",
        "restart_started": "De app wordt opnieuw geinitialiseerd. Wacht tot de server opnieuw is opgestart.",
        "invalid_content_length": "Ongeldige Content-Length-header.",
        "invalid_json": "De request-body moet geldige JSON zijn.",
        "json_object_required": "De request-body moet een JSON-object zijn.",
        "unexpected_answers_payload": "OpenAI gaf een onverwachte antwoordenpayload terug.",
        "malformed_answer_entry": "OpenAI gaf een ongeldig antwoorditem terug.",
        "empty_answer": "OpenAI gaf een leeg antwoord terug.",
    },
}


def normalize_language(raw_language: object) -> str:
    language = str(raw_language or "").strip().lower()
    return language if language in SUPPORTED_LANGUAGES else str(
        EDITION_PROFILES[DEFAULT_EDITION]["language"]
    )


def legacy_language_to_edition(raw_language: object) -> str:
    language = normalize_language(raw_language)
    if language == "nl":
        return "netherlands_nl"
    return DEFAULT_EDITION


def normalize_edition(raw_edition: object) -> str:
    edition = str(raw_edition or "").strip().lower()
    if edition in EDITION_PROFILES:
        return edition
    return legacy_language_to_edition(raw_edition)


def edition_profile(edition: str) -> dict[str, object]:
    return EDITION_PROFILES[normalize_edition(edition)]


def edition_language(edition: str) -> str:
    return str(edition_profile(edition)["language"])


def edition_region(edition: str) -> str:
    return str(edition_profile(edition)["region"])


def preferred_wikipedia_language(edition: str) -> str | None:
    normalized_edition = normalize_edition(edition)
    if edition_region(normalized_edition) == "international":
        return None
    return edition_language(normalized_edition)


def localized_reporting_questions(edition: str) -> list[str]:
    return list(edition_profile(edition)["defaults"]["questions"])


def localized_article_query(edition: str) -> str:
    return str(edition_profile(edition)["defaults"]["article_query"])


def backend_text(language: str, key: str, **kwargs: object) -> str:
    template = BACKEND_TEXT[normalize_language(language)].get(key, key)
    return template.format(**kwargs)


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


def default_reporting_config() -> dict[str, object]:
    edition = DEFAULT_EDITION
    return {
        "edition": edition,
        "language": edition_language(edition),
        "questions": localized_reporting_questions(edition),
        "article_query": localized_article_query(edition),
        "article_word_count": DEFAULT_ARTICLE_WORD_COUNT,
        "article_selection_mode": DEFAULT_ARTICLE_SELECTION_MODE,
    }


def normalize_questions(raw_questions: object) -> list[str]:
    normalized: list[str] = []
    if not isinstance(raw_questions, list):
        return normalized

    for raw_question in raw_questions:
        question = str(raw_question).strip()
        if question:
            normalized.append(question)

    return normalized


def normalize_article_query(raw_query: object) -> str:
    return str(raw_query or "").strip()


def normalize_article_word_count(raw_count: object) -> int:
    try:
        count = int(str(raw_count).strip())
    except (TypeError, ValueError):
        return DEFAULT_ARTICLE_WORD_COUNT

    return count if count > 0 else DEFAULT_ARTICLE_WORD_COUNT


def normalize_article_selection_mode(raw_mode: object) -> str:
    mode = str(raw_mode or "").strip().lower()
    return mode if mode in ARTICLE_SELECTION_MODES else DEFAULT_ARTICLE_SELECTION_MODE


def render_article_query(article_query: str, word_count: int) -> str:
    normalized_query = str(article_query or "").strip()
    normalized_count = normalize_article_word_count(word_count)

    if re.search(rf"\b{re.escape(ARTICLE_WORD_PLACEHOLDER)}\b", normalized_query):
        return re.sub(
            rf"\b{re.escape(ARTICLE_WORD_PLACEHOLDER)}\b",
            str(normalized_count),
            normalized_query,
        )

    return f"{normalized_query.rstrip()} Target length: around {normalized_count} words."


def article_query_uses_placeholder(article_query: str) -> bool:
    return bool(re.search(rf"\b{re.escape(ARTICLE_WORD_PLACEHOLDER)}\b", article_query))


def read_reporting_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return default_reporting_config()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_reporting_config()

    if not isinstance(payload, dict):
        return default_reporting_config()

    edition = normalize_edition(payload.get("edition", payload.get("language")))
    language = edition_language(edition)
    questions = normalize_questions(payload.get("questions"))
    if not questions:
        questions = localized_reporting_questions(edition)

    article_query = normalize_article_query(payload.get("article_query"))
    if not article_query:
        article_query = localized_article_query(edition)
    article_word_count = normalize_article_word_count(payload.get("article_word_count"))
    article_selection_mode = normalize_article_selection_mode(payload.get("article_selection_mode"))

    return {
        "edition": edition,
        "language": language,
        "questions": questions,
        "article_query": article_query,
        "article_word_count": article_word_count,
        "article_selection_mode": article_selection_mode,
    }


def write_reporting_config(
    path: Path,
    edition: str,
    questions: list[str],
    article_query: str,
    article_word_count: int,
    article_selection_mode: str,
) -> None:
    normalized_edition = normalize_edition(edition)
    payload = {
        "edition": normalized_edition,
        "questions": questions,
        "article_query": normalize_article_query(article_query) or localized_article_query(normalized_edition),
        "article_word_count": normalize_article_word_count(article_word_count),
        "article_selection_mode": normalize_article_selection_mode(article_selection_mode),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ensure_local_reporting_config() -> None:
    if REPORTING_CONFIG_FILE.exists():
        return

    write_reporting_config(
        REPORTING_CONFIG_FILE,
        DEFAULT_EDITION,
        localized_reporting_questions(DEFAULT_EDITION),
        localized_article_query(DEFAULT_EDITION),
        DEFAULT_ARTICLE_WORD_COUNT,
        DEFAULT_ARTICLE_SELECTION_MODE,
    )


def extract_openai_error_message(raw_error: bytes, language: str) -> str:
    try:
        payload = json.loads(raw_error)
    except json.JSONDecodeError:
        return raw_error.decode("utf-8", errors="replace") or backend_text(
            language, "openai_error_default"
        )

    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message", backend_text(language, "openai_error_default")))
    return backend_text(language, "openai_error_default")


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


def fetch_available_models(language: str) -> tuple[list[str], str | None]:
    current = current_model()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return [current], backend_text(language, "models_api_key_missing")

    request = Request(
        OPENAI_MODELS_API_URL,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return [current], extract_openai_error_message(error.read(), language)
    except URLError as error:
        return [current], backend_text(language, "could_not_reach_openai", reason=error.reason)

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


def build_briefing_prompt(topic: str, questions: list[str], edition: str) -> str:
    numbered_questions = "\n".join(
        f"{index}. {question}" for index, question in enumerate(questions, start=1)
    )

    if normalize_edition(edition) == "netherlands_nl":
        return (
            "Je bent een journalistieke onderzoeksassistent voor Nederland. Gebruik web search om elke "
            "verslaggeversvraag over het opgegeven onderwerp of de gebeurtenis te beantwoorden. Geef sterke "
            "voorkeur aan Nederlandstalige bronnen en bronnen uit Nederland, waaronder Nederlandse "
            "nieuwsorganisaties, Nederlandse overheidsinstellingen, Nederlandse toezichthouders, Nederlandse "
            "rechtbanken en Nederlandse onderzoeksinstellingen wanneer relevant. Gebruik niet-Nederlandse "
            "bronnen alleen als Nederlandse dekking onvoldoende is of als een primaire bron elders noodzakelijk "
            "is. Laat zulke niet-Nederlandse bronnen niet domineren als er bruikbare Nederlandse bronnen zijn. "
            "Schrijf beknopte, feitelijke briefingteksten voor een journalist. Wees expliciet over onzekerheid, "
            "maak onderscheid tussen wat bekend is en wat onduidelijk blijft, en verzin geen bronnen, citaten "
            "of details. Antwoord in het Nederlands. Geef uitsluitend geldige JSON terug, met exact deze "
            "structuur: "
            '{"answers":[{"question":"<kopieer de oorspronkelijke vraag>","answer":"<antwoord zonder URLs>",'
            '"links":["<relevante bron-url>"]}]}. Houd de vragen in dezelfde volgorde en neem elke vraag precies '
            "een keer op. Zet bron-URLs alleen in de links-array, nooit in de antwoordtekst. Als een antwoord "
            "geen relevante bron-url heeft, geef dan een lege links-array terug."
            "\n\n"
            f"Onderwerp of gebeurtenis:\n{topic}\n\n"
            f"Verslaggeversvragen:\n{numbered_questions}\n"
        )

    return (
        "You are a journalism research assistant. Use web search to answer each reporting question about the "
        "provided topic or event. Prioritize the most authoritative and relevant sources internationally. Do not "
        "restrict yourself to English-language sources when primary or better sources exist in other languages, "
        "but write the final briefing in English. Write concise, factual briefings for a reporter. Be explicit "
        "about uncertainty, distinguish what is known from what remains unclear, and do not invent sources, "
        "quotes, or details. Return valid JSON only, with "
        'this exact shape: {"answers":[{"question":"<copy the original question>","answer":"<answer with no URLs>",'
        '"links":["<relevant source url>"]}]}. Keep the questions in the same order and include every question '
        "exactly once. Put source URLs only in the links array, never inside the answer text. If an answer has no "
        "relevant source URL to list, return an empty links array."
        "\n\n"
        f"Topic or event:\n{topic}\n\n"
        f"Reporting questions:\n{numbered_questions}\n"
    )


def extract_urls(text: str) -> list[str]:
    def clean_url(candidate: str) -> str:
        cleaned = candidate.rstrip(".,;:!?")
        while cleaned.endswith(")") and cleaned.count(")") > cleaned.count("("):
            cleaned = cleaned[:-1]
        while cleaned.endswith("]") and cleaned.count("]") > cleaned.count("["):
            cleaned = cleaned[:-1]
        while cleaned.endswith("}") and cleaned.count("}") > cleaned.count("{"):
            cleaned = cleaned[:-1]
        return cleaned

    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = clean_url(match.group(0))
        if url:
            urls.append(url)
    return urls


def unique_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    return deduped


def normalize_source_title_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = HTML_COMMENT_PATTERN.sub(" ", text)
    text = HTML_TAG_PATTERN.sub(" ", text)
    return HTML_WHITESPACE_PATTERN.sub(" ", text).strip()


def parse_html_attributes(tag: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for name, value in HTML_ATTR_PATTERN.findall(tag):
        cleaned = value.strip().strip("\"'")
        attributes[name.lower()] = html.unescape(cleaned)
    return attributes


def normalize_source_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def hostname_variants(url: str) -> tuple[str, str]:
    try:
        hostname = urlparse(url).netloc.lower()
    except ValueError:
        return "", ""

    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname, hostname.split(".", 1)[0] if hostname else ""


def looks_like_site_name(value: str, url: str) -> bool:
    normalized_value = normalize_source_key(value)
    if not normalized_value:
        return False

    hostname, root = hostname_variants(url)
    normalized_hostname = normalize_source_key(hostname)
    normalized_root = normalize_source_key(root)
    candidates = {
        normalized_hostname,
        normalized_root,
        f"{normalized_root}news" if normalized_root else "",
        f"{normalized_root}nieuws" if normalized_root else "",
    }
    return normalized_value in {candidate for candidate in candidates if candidate}


def looks_like_privacy_gate_title(title: str) -> bool:
    lowered = normalize_source_title_text(title).lower()
    if not lowered:
        return False

    return any(pattern in lowered for pattern in PRIVACY_GATE_PATTERNS)


def title_is_hexish(title: str) -> bool:
    return bool(HEXISH_TITLE_PATTERN.fullmatch(normalize_source_key(title)))


def source_title_is_unusable(title: str, url: str) -> bool:
    normalized_title = normalize_source_title_text(title)
    if not normalized_title:
        return True

    if normalized_title == url:
        return True

    if title_is_hexish(normalized_title):
        return True

    return looks_like_privacy_gate_title(normalized_title)


def source_title_is_article_like(title: str, url: str) -> bool:
    normalized_title = normalize_source_title_text(title)
    if source_title_is_unusable(normalized_title, url):
        return False

    return not looks_like_site_name(normalized_title, url)


def strip_site_suffix(title: str, url: str) -> str:
    cleaned = normalize_source_title_text(title)
    if not cleaned:
        return ""

    for separator in TITLE_SEPARATORS:
        parts = [part.strip() for part in cleaned.split(separator)]
        if len(parts) < 2:
            continue

        trailing = parts[-1]
        leading = separator.join(parts[:-1]).strip()
        if leading and looks_like_site_name(trailing, url):
            return leading

    return cleaned


def derive_source_title_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
    except ValueError:
        return ""

    segments = [
        segment
        for segment in (part.strip() for part in path.split("/"))
        if segment and re.search(r"[a-z]", segment, re.IGNORECASE)
    ]
    generic_segments = {"article", "articles", "news", "story", "stories", "world", "nl", "en"}

    for segment in reversed(segments):
        if segment.lower() in generic_segments:
            continue

        candidate = (
            segment.replace("_", " ")
            .replace("-", " ")
            .replace("+", " ")
            .replace(".html", " ")
        )
        candidate = HTML_WHITESPACE_PATTERN.sub(" ", candidate).strip(" /")
        candidate = re.sub(r"^\d+\s+", "", candidate).strip()
        if len(candidate) < 6:
            continue
        if title_is_hexish(candidate):
            continue
        if looks_like_privacy_gate_title(candidate):
            continue
        if normalize_source_key(candidate).isdigit():
            continue

        return candidate[:1].upper() + candidate[1:]

    return ""


def hostname_display_title(url: str) -> str:
    hostname, _ = hostname_variants(url)
    return hostname


def wikipedia_article_title(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host.endswith(".wikipedia.org"):
        return None

    if parsed.path.startswith("/wiki/"):
        title = parsed.path[len("/wiki/") :]
    else:
        query = parse_qs(parsed.query)
        values = query.get("title", [])
        title = str(values[0]) if values else ""

    title = title.strip()
    if not title:
        return None

    return host, title.replace("_", " ")


def extract_json_ld_article_titles(document: str) -> list[str]:
    candidates: list[str] = []

    def collect_titles(node: object) -> None:
        if isinstance(node, dict):
            headline = node.get("headline")
            if isinstance(headline, str):
                candidates.append(headline)

            alternative_headline = node.get("alternativeHeadline")
            if isinstance(alternative_headline, str):
                candidates.append(alternative_headline)

            for value in node.values():
                collect_titles(value)
            return

        if isinstance(node, list):
            for item in node:
                collect_titles(item)

    for match in JSON_LD_SCRIPT_PATTERN.finditer(document):
        payload = html.unescape(match.group(1).strip())
        if not payload:
            continue

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue

        collect_titles(parsed)

    return [normalize_source_title_text(candidate) for candidate in candidates if candidate]


def extract_meta_title(document: str, names: set[str]) -> str:
    target_names = {name.lower() for name in names}
    for match in META_TAG_PATTERN.finditer(document):
        attributes = parse_html_attributes(match.group(0))
        attribute_name = (attributes.get("property") or attributes.get("name") or "").lower()
        if attribute_name not in target_names:
            continue

        content = normalize_source_title_text(attributes.get("content", ""))
        if content:
            return content

    return ""


def extract_tag_text(document: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(document)
    if not match:
        return ""
    return normalize_source_title_text(match.group(1))


def decode_html_document(raw_bytes: bytes, charset: str | None) -> str:
    encodings: list[str] = []
    if charset:
        encodings.append(charset)
    encodings.extend(["utf-8", "utf-8-sig", "latin-1"])

    seen: set[str] = set()
    for encoding in encodings:
        normalized_encoding = str(encoding or "").strip().lower()
        if not normalized_encoding or normalized_encoding in seen:
            continue
        seen.add(normalized_encoding)

        try:
            return raw_bytes.decode(normalized_encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_bytes.decode("utf-8", errors="replace")


def fetch_source_page_title(url: str) -> str:
    if url in SOURCE_TITLE_CACHE:
        return SOURCE_TITLE_CACHE[url]

    article = wikipedia_article_title(url)
    if article is not None:
        SOURCE_TITLE_CACHE[url] = article[1]
        return article[1]

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        SOURCE_TITLE_CACHE[url] = ""
        return ""

    request = Request(
        url,
        method="GET",
        headers={
            "User-Agent": APP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with urlopen(request, timeout=10) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "html" not in content_type:
                SOURCE_TITLE_CACHE[url] = ""
                return ""
            document = decode_html_document(
                response.read(750_000),
                response.headers.get_content_charset(),
            )
    except (HTTPError, URLError, TimeoutError, OSError):
        SOURCE_TITLE_CACHE[url] = ""
        return ""

    candidate_titles = [
        extract_tag_text(document, H1_TAG_PATTERN),
        extract_meta_title(document, {"og:title"}),
        extract_meta_title(document, {"twitter:title"}),
        *extract_json_ld_article_titles(document),
        extract_tag_text(document, TITLE_TAG_PATTERN),
    ]

    best_fallback = ""
    for candidate in candidate_titles:
        cleaned = strip_site_suffix(candidate, url)
        if not cleaned:
            continue

        if source_title_is_article_like(cleaned, url):
            SOURCE_TITLE_CACHE[url] = cleaned
            return cleaned

        if not best_fallback and not source_title_is_unusable(cleaned, url):
            best_fallback = cleaned

    if best_fallback:
        SOURCE_TITLE_CACHE[url] = best_fallback
        return best_fallback

    SOURCE_TITLE_CACHE[url] = ""
    return ""


def resolve_source_title(url: str, raw_title: object) -> str:
    article = wikipedia_article_title(url)
    if article is not None:
        return article[1]

    supplied_title = strip_site_suffix(str(raw_title or ""), url)
    if supplied_title and source_title_is_article_like(supplied_title, url):
        return supplied_title

    fetched_title = fetch_source_page_title(url)
    if fetched_title and source_title_is_article_like(fetched_title, url):
        return fetched_title

    if supplied_title and not source_title_is_unusable(supplied_title, url):
        return supplied_title

    if fetched_title and not source_title_is_unusable(fetched_title, url):
        return fetched_title

    derived_title = derive_source_title_from_url(url)
    if derived_title:
        return derived_title

    hostname_title = hostname_display_title(url)
    if hostname_title:
        return hostname_title

    return ""


def resolve_wikipedia_language_variant(url: str, target_language: str | None) -> str:
    normalized_language = str(target_language or "").strip().lower()
    if not normalized_language:
        return url

    cache_key = (url, normalized_language)
    if cache_key in WIKIPEDIA_LINK_CACHE:
        return WIKIPEDIA_LINK_CACHE[cache_key]

    article = wikipedia_article_title(url)
    if article is None:
        WIKIPEDIA_LINK_CACHE[cache_key] = url
        return url

    host, title = article
    current_language = host.split(".", 1)[0]
    if current_language == normalized_language:
        WIKIPEDIA_LINK_CACHE[cache_key] = url
        return url

    api_url = (
        f"https://{host}/w/api.php?"
        + urlencode(
            {
                "action": "query",
                "titles": title,
                "prop": "langlinks",
                "lllang": normalized_language,
                "lllimit": 1,
                "llprop": "url",
                "redirects": 1,
                "format": "json",
                "formatversion": 2,
            }
        )
    )
    request = Request(
        api_url,
        method="GET",
        headers={"User-Agent": APP_USER_AGENT},
    )

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError):
        WIKIPEDIA_LINK_CACHE[cache_key] = url
        return url

    pages = payload.get("query", {}).get("pages", [])
    if not isinstance(pages, list):
        WIKIPEDIA_LINK_CACHE[cache_key] = url
        return url

    for page in pages:
        if not isinstance(page, dict):
            continue

        langlinks = page.get("langlinks", [])
        if not isinstance(langlinks, list):
            continue

        for langlink in langlinks:
            if not isinstance(langlink, dict):
                continue

            resolved_url = str(langlink.get("url", "")).strip()
            if resolved_url:
                WIKIPEDIA_LINK_CACHE[cache_key] = resolved_url
                return resolved_url

            resolved_title = str(langlink.get("title", "") or langlink.get("*", "")).strip()
            if resolved_title:
                resolved_url = (
                    f"https://{normalized_language}.wikipedia.org/wiki/"
                    f"{quote(resolved_title.replace(' ', '_'), safe=':/()')}"
                )
                WIKIPEDIA_LINK_CACHE[cache_key] = resolved_url
                return resolved_url

    WIKIPEDIA_LINK_CACHE[cache_key] = url
    return url


def normalize_links_for_edition(urls: list[str], edition: str) -> list[str]:
    target_language = preferred_wikipedia_language(edition)
    return unique_urls(
        [resolve_wikipedia_language_variant(url, target_language) for url in urls]
    )


def normalize_answer_links(raw_links: object) -> list[str]:
    if not isinstance(raw_links, list):
        return []

    urls: list[str] = []
    for item in raw_links:
        urls.extend(extract_urls(str(item)))

    return unique_urls(urls)


def split_answer_links(answer: str) -> tuple[str, list[str]]:
    lines = answer.strip().splitlines()
    trailing_urls: list[str] = []
    end_index = len(lines)

    while end_index > 0:
        line = lines[end_index - 1].strip()
        if not line:
            end_index -= 1
            continue

        lower = line.lower()
        if lower in {"links:", "link:", "sources:", "source:"}:
            end_index -= 1
            break

        urls = extract_urls(line)
        if not urls:
            break

        remainder = URL_PATTERN.sub("", line)
        remainder = re.sub(r"[\s,*\-•]+", " ", remainder).strip()
        if remainder and remainder.lower().rstrip(":") not in {"links", "link", "sources", "source"}:
            break

        trailing_urls = urls + trailing_urls
        end_index -= 1

    cleaned_answer = "\n".join(lines[:end_index]).strip()
    return cleaned_answer or answer.strip(), unique_urls(trailing_urls)


def parse_briefing_output(text: str, questions: list[str], edition: str) -> list[dict[str, object]]:
    language = edition_language(edition)
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
        raise ValueError(backend_text(language, "unexpected_answers_payload"))

    normalized_answers: list[dict[str, object]] = []
    for question, item in zip(questions, answers):
        if not isinstance(item, dict):
            raise ValueError(backend_text(language, "malformed_answer_entry"))

        answer = str(item.get("answer", "")).strip()
        if not answer:
            raise ValueError(backend_text(language, "empty_answer"))

        cleaned_answer, trailing_links = split_answer_links(answer)
        if not cleaned_answer:
            raise ValueError(backend_text(language, "empty_answer"))

        explicit_links = normalize_answer_links(item.get("links"))
        normalized_answers.append(
            {
                "question": question,
                "answer": cleaned_answer,
                "links": normalize_links_for_edition(explicit_links + trailing_links, edition),
            }
        )

    return normalized_answers


def normalize_article_excerpts(raw_excerpts: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(raw_excerpts, list):
        return normalized

    for item in raw_excerpts:
        if not isinstance(item, dict):
            continue

        question = str(item.get("question", "")).strip()
        text = str(item.get("text", "")).strip()
        if not question or not text:
            continue

        normalized.append({"question": question, "text": text})

    return normalized


def normalize_snapshot_answers(raw_answers: object) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    if not isinstance(raw_answers, list):
        return normalized

    for item in raw_answers:
        if not isinstance(item, dict):
            continue

        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question or not answer:
            continue

        normalized.append(
            {
                "question": question,
                "answer": answer,
                "links": normalize_answer_links(item.get("links")),
            }
        )

    return normalized


def normalize_snapshot_sources(raw_sources: object, edition: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(raw_sources, list):
        return normalized

    target_language = preferred_wikipedia_language(edition)
    seen_urls: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, dict):
            continue

        raw_url = str(item.get("url", "")).strip()
        if not raw_url:
            continue

        url = resolve_wikipedia_language_variant(raw_url, target_language)
        if not url or url in seen_urls:
            continue

        seen_urls.add(url)
        normalized.append({"title": resolve_source_title(url, item.get("title")), "url": url})

    return normalized


def build_snapshot_translation_prompt(
    answers: list[dict[str, object]], article: str, target_language: str
) -> str:
    snapshot_payload = json.dumps(
        {
            "answers": [
                {
                    "question": str(item["question"]),
                    "answer": str(item["answer"]),
                }
                for item in answers
            ],
            "article": article,
        },
        ensure_ascii=False,
    )

    if normalize_language(target_language) == "nl":
        return (
            "Je bent een vertaalassistent voor journalistieke teksten. Vertaal de volledige snapshot getrouw "
            "naar het Nederlands. Behoud betekenis, nuance, onzekerheid, structuur en alinea-indeling. Voeg "
            "geen feiten toe, laat niets weg en vat niet samen. Geef uitsluitend geldige JSON terug met exact "
            'deze vorm: {"answers":[{"question":"...","answer":"..."}],"article":"..."}. Houd hetzelfde aantal '
            "answers in dezelfde volgorde. Als article leeg is, geef een lege string terug."
            "\n\n"
            f"Snapshot:\n{snapshot_payload}\n"
        )

    return (
        "You are a translation assistant for journalism workflows. Translate the full snapshot faithfully into "
        "English. Preserve meaning, nuance, uncertainty, structure, and paragraph breaks. Do not add facts, do "
        "not omit facts, and do not summarize. Return valid JSON only with this exact shape: "
        '{"answers":[{"question":"...","answer":"..."}],"article":"..."}. Keep the same number of answers in the '
        "same order. If article is empty, return an empty string."
        "\n\n"
        f"Snapshot:\n{snapshot_payload}\n"
    )


def build_topic_translation_prompt(topic: str, target_language: str) -> str:
    normalized_language = normalize_language(target_language)
    if normalized_language == "nl":
        return (
            "Je bent een vertaalassistent voor journalistieke workflows. Vertaal uitsluitend de opgegeven "
            "onderwerp- of gebeurtenisregel naar natuurlijk Nederlands. Behoud eigennamen, data, onzekerheid en "
            "bedoeling. Beantwoord de vraag niet, voeg geen context toe en parafraseer niet verder dan nodig is "
            "voor een natuurlijke Nederlandse formulering. Geef uitsluitend geldige JSON terug met exact deze "
            'vorm: {"text":"..."}.'
            "\n\n"
            f"Onderwerp of gebeurtenis:\n{topic}\n"
        )

    return (
        "You are a translation assistant for journalism workflows. Translate only the provided topic or event line "
        "into natural English. Preserve names, dates, uncertainty, and intent. Do not answer the query, do not add "
        "context, and do not paraphrase beyond what is needed for natural English wording. Return valid JSON only "
        'with this exact shape: {"text":"..."}.'
        "\n\n"
        f"Topic or event:\n{topic}\n"
    )


def parse_snapshot_translation_output(
    text: str, expected_answers: int, language: str
) -> dict[str, object]:
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
    if not isinstance(answers, list) or len(answers) != expected_answers:
        raise ValueError(backend_text(language, "unexpected_answers_payload"))

    normalized_answers: list[dict[str, str]] = []
    for item in answers:
        if not isinstance(item, dict):
            raise ValueError(backend_text(language, "malformed_answer_entry"))

        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question or not answer:
            raise ValueError(backend_text(language, "empty_answer"))

        normalized_answers.append({"question": question, "answer": answer})

    article = str(payload.get("article", "")).strip()
    return {"answers": normalized_answers, "article": article}


def parse_topic_translation_output(text: str, language: str) -> str:
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
    translated = str(payload.get("text", "")).strip()
    if not translated:
        raise ValueError(backend_text(language, "provide_topic"))

    return translated


def build_article_prompt(article_query: str, excerpts: list[dict[str, str]], edition: str) -> str:
    instruction = article_query.strip() or localized_article_query(edition)
    excerpt_block = "\n\n".join(
        f"Excerpt {index}\nQuestion: {item['question']}\nText: {item['text']}"
        for index, item in enumerate(excerpts, start=1)
    )

    if normalize_edition(edition) == "netherlands_nl":
        return (
            "Je bent een newsroom-assistent. Schrijf een nieuwsartikel met uitsluitend de opgegeven "
            "bronfragmenten. Voeg geen feiten, citaten, toeschrijvingen, data, cijfers of context toe die niet "
            "expliciet in de fragmenten staan. Laat belangrijke feiten weg als ze ontbreken, in plaats van te "
            "gissen. Schrijf alleen de artikeltekst, in het Nederlands."
            "\n\n"
            f"Artikelopdracht:\n{instruction}\n\n"
            f"Bronfragmenten:\n{excerpt_block}\n"
        )

    return (
        "You are a newsroom writing assistant. Draft a news article using only the provided source excerpts. "
        "Do not add facts, quotes, attributions, dates, numbers, or context that are not explicitly present in the "
        "excerpts. If important facts are missing, leave them out rather than guessing. Write only the article text "
        "in English."
        "\n\n"
        f"Article request:\n{instruction}\n\n"
        f"Source excerpts:\n{excerpt_block}\n"
    )


def extract_web_search_sources(payload: dict[str, object], edition: str) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    sources: list[dict[str, str]] = []

    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue

        action = item.get("action", {})
        if not isinstance(action, dict):
            continue

        raw_sources = action.get("sources", [])
        if not isinstance(raw_sources, list):
            continue

        for source in raw_sources:
            if not isinstance(source, dict):
                continue

            url = resolve_wikipedia_language_variant(
                str(source.get("url", "")).strip(),
                preferred_wikipedia_language(edition),
            )
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            sources.append({"title": resolve_source_title(url, source.get("title")), "url": url})

    return sources


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
                if process.returncode == RESTART_EXIT_CODE:
                    print("App restart requested. Restarting server...", flush=True)
                    process = start_server_process()
                    waiting_for_change = False
                    snapshot = take_watch_snapshot()
                    continue

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


class RestartableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, request_handler_class):
        super().__init__(server_address, request_handler_class)
        self.restart_requested = False


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_HEAD(self) -> None:
        self._map_static_route()
        super().do_HEAD()

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        language = reporting_config["language"]

        if route == "/api/hello":
            self._send_json(
                200,
                {
                    "message": backend_text(language, "hello_message"),
                    "model": current_model(),
                    "instance_id": SERVER_INSTANCE_ID,
                    "edition": reporting_config["edition"],
                    "language": language,
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

        if route == "/api/article":
            self._handle_article_request()
            return

        if route == "/api/translate-topic":
            self._handle_topic_translation_request()
            return

        if route == "/api/translate-snapshot":
            self._handle_snapshot_translation_request()
            return

        if route == "/api/normalize-sources":
            self._handle_source_normalization_request()
            return

        if route == "/api/config":
            self._handle_config_update()
            return

        if route == "/api/restart":
            self._handle_restart_request()
            return

        self._send_json(404, {"error": backend_text(read_reporting_config(REPORTING_CONFIG_FILE)["language"], "route_not_found")})

    def _handle_briefing_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        edition = reporting_config["edition"]
        language = reporting_config["language"]

        topic = str(payload.get("query", "")).strip()
        if not topic:
            self._send_json(400, {"error": backend_text(language, "provide_topic")})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": backend_text(language, "api_key_missing")})
            return

        model = current_model()
        reporting_questions = reporting_config["questions"]
        request_body = json.dumps(
            {
                "model": model,
                "tools": [{"type": "web_search"}],
                "tool_choice": "auto",
                "include": ["web_search_call.action.sources"],
                "input": build_briefing_prompt(topic, reporting_questions, edition),
            }
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
            message = extract_openai_error_message(error.read(), language)
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(
                502, {"error": backend_text(language, "could_not_reach_openai", reason=error.reason)}
            )
            return

        raw_output = extract_output_text(upstream_payload)
        if not raw_output:
            self._send_json(502, {"error": backend_text(language, "no_text_output")})
            return

        try:
            answers = parse_briefing_output(raw_output, reporting_questions, edition)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(502, {"error": backend_text(language, "invalid_briefing_payload", error=error)})
            return

        sources = extract_web_search_sources(upstream_payload, edition)

        self._send_json(
            200,
            {
                "model": model,
                "topic": topic,
                "answers": answers,
                "sources": sources,
            },
        )

    def _handle_article_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        edition = reporting_config["edition"]
        language = reporting_config["language"]
        article_query = render_article_query(
            str(reporting_config["article_query"]),
            int(reporting_config["article_word_count"]),
        )
        article_selection_mode = normalize_article_selection_mode(reporting_config["article_selection_mode"])
        excerpts = normalize_article_excerpts(payload.get("excerpts"))
        if not excerpts:
            excerpts = normalize_article_excerpts(payload.get("highlights"))

        if not excerpts:
            self._send_json(400, {"error": backend_text(language, "no_excerpts")})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": backend_text(language, "api_key_missing")})
            return

        model = current_model()
        request_body = json.dumps(
            {"model": model, "input": build_article_prompt(article_query, excerpts, edition)}
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
            message = extract_openai_error_message(error.read(), language)
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(
                502, {"error": backend_text(language, "could_not_reach_openai", reason=error.reason)}
            )
            return

        article = extract_output_text(upstream_payload)
        if not article:
            self._send_json(502, {"error": backend_text(language, "no_article_text")})
            return

        self._send_json(
            200,
            {
                "model": model,
                "article": article,
                "excerpt_count": len(excerpts),
                "article_query": article_query,
                "article_word_count": reporting_config["article_word_count"],
                "article_selection_mode": article_selection_mode,
            },
        )

    def _handle_source_normalization_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        language = reporting_config["language"]
        edition = normalize_edition(payload.get("edition") or payload.get("snapshot_edition"))
        sources = normalize_snapshot_sources(payload.get("sources"), edition)
        self._send_json(
            200,
            {
                "sources": sources,
                "edition": edition,
                "language": edition_language(edition) or language,
            },
        )

    def _handle_snapshot_translation_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        edition = reporting_config["edition"]
        language = reporting_config["language"]
        answers = normalize_snapshot_answers(payload.get("answers"))
        article = str(payload.get("article", "")).strip()
        sources = normalize_snapshot_sources(payload.get("sources"), edition)

        if not answers:
            self._send_json(400, {"error": backend_text(language, "no_snapshot_answers")})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": backend_text(language, "api_key_missing")})
            return

        model = current_model()
        request_body = json.dumps(
            {
                "model": model,
                "input": build_snapshot_translation_prompt(answers, article, language),
            }
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
            message = extract_openai_error_message(error.read(), language)
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(
                502, {"error": backend_text(language, "could_not_reach_openai", reason=error.reason)}
            )
            return

        raw_output = extract_output_text(upstream_payload)
        if not raw_output:
            self._send_json(502, {"error": backend_text(language, "no_text_output")})
            return

        try:
            translated = parse_snapshot_translation_output(raw_output, len(answers), language)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(
                502, {"error": backend_text(language, "invalid_translation_payload", error=error)}
            )
            return

        translated_answers: list[dict[str, object]] = []
        for original, translated_item in zip(answers, translated["answers"]):
            translated_answers.append(
                {
                    "question": translated_item["question"],
                    "answer": translated_item["answer"],
                    "links": normalize_links_for_edition(list(original["links"]), edition),
                }
            )

        self._send_json(
            200,
            {
                "model": model,
                "answers": translated_answers,
                "article": translated["article"],
                "sources": sources,
                "snapshot_edition": str(payload.get("snapshot_edition", "")).strip(),
                "target_edition": edition,
                "language": language,
            },
        )

    def _handle_topic_translation_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        language = reporting_config["language"]
        topic = str(payload.get("topic", "")).strip()
        if not topic:
            self._send_json(400, {"error": backend_text(language, "provide_topic")})
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            self._send_json(500, {"error": backend_text(language, "api_key_missing")})
            return

        model = current_model()
        request_body = json.dumps(
            {
                "model": model,
                "input": build_topic_translation_prompt(topic, language),
            }
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
            message = extract_openai_error_message(error.read(), language)
            self._send_json(error.code, {"error": message})
            return
        except URLError as error:
            self._send_json(
                502, {"error": backend_text(language, "could_not_reach_openai", reason=error.reason)}
            )
            return

        raw_output = extract_output_text(upstream_payload)
        if not raw_output:
            self._send_json(502, {"error": backend_text(language, "no_text_output")})
            return

        try:
            translated_topic = parse_topic_translation_output(raw_output, language)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(
                502, {"error": backend_text(language, "invalid_translation_payload", error=error)}
            )
            return

        self._send_json(
            200,
            {
                "model": model,
                "topic": translated_topic,
                "language": language,
            },
        )

    def _handle_config_update(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        current_config = read_reporting_config(REPORTING_CONFIG_FILE)
        previous_edition = current_config["edition"]
        edition = normalize_edition(payload.get("edition", payload.get("language", previous_edition)))
        language = edition_language(edition)
        model = str(payload.get("openai_model", current_model())).strip()
        if not model:
            self._send_json(400, {"error": backend_text(language, "model_empty")})
            return

        questions = normalize_questions(payload.get("questions"))
        if not questions:
            self._send_json(400, {"error": backend_text(language, "add_question")})
            return

        article_query = normalize_article_query(payload.get("article_query"))
        if not article_query:
            article_query = localized_article_query(edition)

        if edition != previous_edition:
            if questions == localized_reporting_questions(previous_edition):
                questions = localized_reporting_questions(edition)
            if article_query == localized_article_query(previous_edition):
                article_query = localized_article_query(edition)

        if not article_query_uses_placeholder(article_query):
            self._send_json(
                400,
                {"error": backend_text(language, "article_query_placeholder")},
            )
            return

        article_word_count = normalize_article_word_count(payload.get("article_word_count"))
        if article_word_count <= 0:
            self._send_json(400, {"error": backend_text(language, "article_word_positive")})
            return

        article_selection_mode = normalize_article_selection_mode(payload.get("article_selection_mode"))

        write_env_file(ENV_FILE, {"OPENAI_MODEL": model})
        os.environ["OPENAI_MODEL"] = model
        write_reporting_config(
            REPORTING_CONFIG_FILE,
            edition,
            questions,
            article_query,
            article_word_count,
            article_selection_mode,
        )

        response_payload = self._config_payload()
        response_payload["message"] = backend_text(language, "config_saved")
        self._send_json(200, response_payload)

    def _handle_restart_request(self) -> None:
        language = read_reporting_config(REPORTING_CONFIG_FILE)["language"]
        self._send_json(
            202,
            {
                "message": backend_text(language, "restart_started"),
                "instance_id": SERVER_INSTANCE_ID,
            },
        )

        if hasattr(self.server, "restart_requested"):
            self.server.restart_requested = True

        threading.Thread(target=self._shutdown_server_after_response, daemon=True).start()

    def _shutdown_server_after_response(self) -> None:
        time.sleep(0.2)
        self.server.shutdown()

    def _read_json_body(self) -> dict[str, object] | None:
        language = read_reporting_config(REPORTING_CONFIG_FILE)["language"]
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": backend_text(language, "invalid_content_length")})
            return None

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": backend_text(language, "invalid_json")})
            return None

        if not isinstance(payload, dict):
            self._send_json(400, {"error": backend_text(language, "json_object_required")})
            return None

        return payload

    def _config_payload(self) -> dict[str, object]:
        reporting_config = read_reporting_config(REPORTING_CONFIG_FILE)
        language = reporting_config["language"]
        available_models, models_error = fetch_available_models(language)

        return {
            "edition": reporting_config["edition"],
            "region": edition_region(reporting_config["edition"]),
            "language": language,
            "openai_model": current_model(),
            "available_models": available_models,
            "questions": reporting_config["questions"],
            "article_query": reporting_config["article_query"],
            "article_word_count": reporting_config["article_word_count"],
            "article_selection_mode": reporting_config["article_selection_mode"],
            "resolved_article_query": render_article_query(
                str(reporting_config["article_query"]),
                int(reporting_config["article_word_count"]),
            ),
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
    server = RestartableHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Serving on http://127.0.0.1:{port}")

    try:
        server.serve_forever()
    finally:
        server.server_close()

    if server.restart_requested:
        if os.environ.get(RELOAD_ENV_VAR) == "1":
            raise SystemExit(RESTART_EXIT_CODE)

        os.execve(sys.executable, [sys.executable, str(ROOT_DIR / "app.py")], os.environ.copy())


def main() -> None:
    if os.environ.get(RELOAD_ENV_VAR) == "1" or os.environ.get(DISABLE_RELOAD_ENV_VAR) == "1":
        serve()
        return

    run_with_reloader()


if __name__ == "__main__":
    main()
