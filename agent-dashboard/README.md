# Agent Dashboard

Health-checker for a fleet of agents. Reads a registry, pings each agent
over HTTP, checks its most recent error log entry, and reports one of four
statuses per agent: `ok`, `lento`, `caído`, `ok-con-error`.

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
    { "name": "router", "ping_url": "http://localhost:8001/health", "timeout_seconds": 5 }
  ]
}
```

- `name`: agent identifier, also used to locate `~/agents/<name>/logs/error.log`.
- `ping_url`: HTTP endpoint the agent exposes; any 2xx response counts as reachable.
- `timeout_seconds`: optional, defaults to 5.

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
