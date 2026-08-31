# Agent Dashboard

Health-checker for a fleet of agents. Reads a registry, checks each agent's
liveness (over HTTP, or via a heartbeat file for agents with no HTTP
surface — e.g. the file-based agents [`agent-router`](../agent-router)
dispatches to), checks its most recent error log entry, and reports one of
four statuses per agent: `ok`, `lento`, `caído`, `ok-con-error`.

## Layout it expects

```
~/agents/
  registry.json            # list of agents to monitor (see registry.example.json)
  <agent-name>/
    logs/
      error.log            # one entry per line, newest last
  dashboard/
    state.json             # internal: last successful ping per agent
    last-status.json        # output of the most recent check
    alerts.log              # appended whenever an agent is found "caído"
```

Set `AGENTS_HOME` to point somewhere other than `~/agents` if needed.

### `registry.json`

```json
{
  "agents": [
    { "name": "email", "heartbeat_file": "~/agents/email/heartbeat" },
    { "name": "legacy-http-agent", "ping_url": "http://localhost:8001/health", "timeout_seconds": 5 }
  ]
}
```

- `name`: agent identifier, also used to locate `~/agents/<name>/logs/error.log`.
- `ping_url`: HTTP endpoint the agent exposes; any 2xx response counts as reachable.
  `timeout_seconds`: optional, defaults to 5.
- `heartbeat_file`: path the agent itself touches/writes to periodically
  (`~` is expanded). Reachable as long as its mtime is within the last 5
  minutes. This is the mechanism for agents with no HTTP endpoint at all —
  it never measures latency, so a heartbeat-only agent can be `ok`,
  `ok-con-error`, or `caído`, but never `lento`.
- Set exactly one of `ping_url` / `heartbeat_file` per agent; if an agent
  has neither, it's always reported `caído`.

### `error.log`

Each line should start with an ISO 8601 timestamp followed by the message,
e.g.:

```
2026-08-31T10:15:00Z connection refused to upstream
```

The dashboard reads the last line as the agent's current error. If the
timestamp can't be parsed, the error is treated as unresolved (conservative
default) so it still surfaces.

## Status rules

- **caído**: no successful ping in the last 5 minutes.
- **lento**: the current ping succeeded but took longer than 30 seconds.
- **ok-con-error**: reachable and fast, but the last line of `error.log` is
  within the last 24 hours.
- **ok**: reachable, fast, no unresolved error.

Precedence: caído > lento > ok-con-error > ok.

## Usage

Run one check and print the status JSON (no extra text):

```bash
python dashboard.py check
```

Output:

```json
{
  "checked_at": "2026-08-31T12:00:00Z",
  "agents": [
    { "name": "router", "status": "ok", "last_ping": "2026-08-31T11:59:58Z", "last_error": null }
  ]
}
```

Run forever, checking every hour and persisting to
`~/agents/dashboard/last-status.json` (plus appending to `alerts.log` on any
"caído" agent):

```bash
python dashboard.py loop
```

Run a single loop iteration (writes the files, then exits) — useful when
driven by cron/systemd-timer instead of the built-in hourly sleep:

```bash
python dashboard.py loop --once
```

## Live viewer

```bash
python dashboard.py serve --port 8080 --interval 10
```

Opens a web page at `http://localhost:8080` showing every registered agent
as a card (name, status, last ping, last error). It runs its own check loop
(default every 10s, independent from `loop`'s hourly cadence) and pushes
each new result to the browser over Server-Sent Events, so a card flips to
"caído" — with a highlight animation — the moment the dashboard detects it,
with no manual refresh. Scales to any number of agents in `registry.json`;
the page lays them out as a responsive grid.

Below the cards, a **Histórico** panel lists every status transition (e.g.
`ok → caído`), newest first, pushed live the moment it's detected — durably
recorded in `~/agents/dashboard/history.jsonl` (one JSON object per line:
`at`, `name`, `from`, `to`) so it survives restarts and is queryable outside
the browser too (`GET /api/history` returns the last 50 as JSON).

## Running it for real (systemd)

`systemd/agent-dashboard.service` + `systemd/agent-dashboard.timer` run
`loop --once` every hour via systemd instead of relying on the script's own
`time.sleep`. Install as a user unit:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/agent-dashboard.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-dashboard.timer
```

## Trying it out locally

`examples/demo_agent.py` is a throwaway HTTP endpoint for exercising every
status:

```bash
python examples/demo_agent.py --port 8001                 # healthy -> ok
python examples/demo_agent.py --port 8002 --fail           # 500s    -> caído (after 5 min without a 2xx)
python examples/demo_agent.py --port 8003 --delay 31       # slow    -> lento
```

Point a `registry.json` at these ports, then run `python dashboard.py check`.

## Tests

```bash
python -m unittest discover -s tests
```
