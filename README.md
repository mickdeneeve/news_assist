# News Assist

News Assist is a local journalism briefing tool with a Python backend and browser UI.

The repository does not include a real OpenAI API key. Use your own local `.env` file.

## Preview

![Briefing page preview](screenshots/scr.be1.png)

See [all screenshots](SCREENSHOTS.md).

## Setup

1. Copy `.env.example` to `.env`.
2. Put your own OpenAI API key in `.env`.
3. Start the app:
   - macOS/Linux: `python3 app.py`
   - Windows: `python app.py`
4. Open `http://127.0.0.1:8000/` in your browser.

## Notes

- `.env` is ignored by git and should not be committed.
- If a stale local News Assist server from this checkout is still holding the configured port, rerunning `python3 app.py` asks it to stop and then continues startup.
- If some other program owns the port, inspect it manually; see [RUNBOOK.md](RUNBOOK.md).

\- by Mick de Neeve (codex-assisted, 2026)
