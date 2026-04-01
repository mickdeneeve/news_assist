# News Assist Runbook

## Normal Start

- Use `python3 app.py` from the repository root.
- If an older News Assist instance from this same checkout is still holding the configured port, startup asks it to stop and then continues.

## When Manual Diagnosis Is Still Needed

- Another program owns the configured port.
- Another News Assist checkout owns the configured port.
- The older instance does not respond to the local takeover request.

## Manual Logic

Use this order when the app still will not start:

1. Check whether the app port is already occupied.
   - `lsof -iTCP:8000 -sTCP:LISTEN -P -n`
2. Find any `app.py` processes.
   - `ps -ef | grep '[a]pp.py'`
3. Inspect the PID before killing it.
   - `ps -fp <PID>`
4. Stop the interfering process cleanly.
   - `kill <PID>`
5. Start the app again.
   - `python3 app.py`

## Project-Specific Note

- A healthy local run usually has two `app.py` processes: a watcher parent and a child server.
- A common broken state is that only the child server remains and still holds the port.
- The underlying diagnostic chain is still `port -> pid -> inspect -> kill -> restart`, even though the normal start command now handles the stale same-checkout case for you.
