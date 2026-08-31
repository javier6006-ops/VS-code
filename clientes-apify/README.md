# Clientes (Apify)

Client-acquisition agent: runs an [Apify](https://apify.com) Actor (a
hosted scraper — Google Maps businesses, LinkedIn, generic websites, etc.)
and turns its output into a deduplicated leads file. Standalone for now —
not yet wired into `agent-router` / `agent-dashboard`, see **Next steps**.

## Status: needs credentials before it can run for real

Same situation as the `email` agent in [`agent-worker`](../agent-worker):
the plumbing is built and tested (with the network mocked out), but two
things are still pending before it does anything live:

1. **`APIFY_API_TOKEN`** — an Apify account + API token
   (https://console.apify.com/settings/integrations).
2. **Which Actor, and what to search for** — depends on the business/ICP,
   which hasn't been defined yet. `search_input.example.json` is a
   placeholder shaped for a Google Maps scraper
   (`compass~crawler-google-places`); the real Actor and its input schema
   should be picked once that's decided.

Nothing here needs those two things to keep developing — the client,
normalization and CLI are all done and tested.

## Files

- `apify_client.py` — minimal Apify REST client (stdlib only, no
  `apify-client` package). `run_actor_sync(actor_id, run_input, token)`
  runs an Actor synchronously and returns its output dataset directly —
  no separate run-status polling needed.
- `leads.py` — `normalize_lead()` maps each Actor's raw item (field names
  vary per Actor) onto a common shape (`name`, `website`, `phone`,
  `email`, `address`, plus the original under `raw`); `dedupe_leads()`
  drops repeats by a chosen key (default `email`); `save_leads_csv` /
  `save_leads_json` write the result out.
- `fetch_leads.py` — CLI tying it together.
- `search_input.example.json` — placeholder Actor input; copy and edit
  once an Actor is picked.

## Usage (once there's a token)

```bash
export APIFY_API_TOKEN=...
cp search_input.example.json search_input.json   # edit the search terms/location
python fetch_leads.py --actor compass~crawler-google-places --input search_input.json --out leads.csv
```

`--dedupe-key` defaults to `email`; pass `--dedupe-key website` (or `""`
to disable) if the chosen Actor doesn't return emails.

## Tests (no token needed — network is mocked)

```bash
python -m unittest discover -s tests
```

## Next steps (not done yet, on purpose)

- Once there's a real Actor + token, add a `handlers/clientes.py` in
  `agent-worker` (mirroring `handlers/email.py`) so this can run as a
  worker agent — polling a `clientes` inbox for "find leads for X" style
  requests instead of only the manual CLI.
- If that happens, `agent-router`'s dispatch table would need a
  `"clientes"` (or similar) entry with its own keywords — that's a
  shared-behavior change worth confirming the keywords for first, so it
  hasn't been made here.
- Add `heartbeat_file` for a `clientes` agent to `agent-dashboard`'s
  registry once it's running as a long-lived worker.
