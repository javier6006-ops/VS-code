# Agent Worker

The other end of [`agent-router`](../agent-router)'s inbox/outbox
convention. Without something like this, the router writes a message to
`~/agents/<agent>/inbox/` and nothing ever answers it — every dispatch
just times out after 5 minutes.

`worker.py` is a generic runner: point it at an agent name and a handler
function, and it polls that agent's inbox, calls your handler on each
message, writes the reply to the matching outbox file, and touches the
agent's heartbeat (so [`agent-dashboard`](../agent-dashboard) sees it as
alive via `heartbeat_file`). Only the handler function differs per agent —
`handlers/email.py` is a first, intentionally stubbed-out example.

## Layout it expects (same `~/agents` tree as agent-router/agent-dashboard)

```
~/agents/
  <agent-name>/
    inbox/
      <token>.md          # written by agent-router
      processed/
        <token>.md         # archived here once handled
    outbox/
      <token>.md          # written by this worker
    logs/
      error.log            # appended when the handler raises
    heartbeat               # touched every run_once(), even with no messages
```

Set `AGENTS_HOME` to point somewhere other than `~/agents` if needed.

## Writing a handler

```python
def handle(message: str, metadata: dict) -> str:
    """metadata has from_user / channel / timestamp (all str)."""
    ...
    return "reply text to send back to the user"
```

Raise any exception on failure — the worker logs it to `logs/error.log`
(which `agent-dashboard` turns into `ok-con-error`) and writes a generic
apology to the outbox instead of leaving the router hanging.

## `handlers/email.py`: a stub, not a real mailbox

It proves the pipeline end-to-end (router → inbox → worker → outbox →
router) with canned replies — it does not read or send real email. Wiring
up an actual mailbox needs real credentials for whichever provider gets
picked (Gmail API OAuth, IMAP/SMTP, ...); that's separate follow-up work.
The `company` and `ecommerce` agents need their own handler modules the
same way, once there's real internal-docs / Shopify access to wire in.

## Usage

```bash
# Long-running: poll every 2s until killed.
python worker.py run --name email --handler handlers.email:handle

# Single pass — process whatever's pending, then exit. Good for cron.
python worker.py run --name email --handler handlers.email:handle --once
```

End-to-end by hand:

```bash
# Terminal 1 (or a background/&-ed process): dispatch and wait for a reply.
cd ../agent-router
python router.py dispatch --message "responde este correo" --from-user alejandro --channel telegram

# Terminal 2: process it.
cd ../agent-worker
python worker.py run --name email --handler handlers.email:handle --once
```

## Tests

```bash
python -m unittest discover -s tests
```

Includes an integration test (`RouterWorkerIntegrationTestCase`) that
imports both `router.py` and `worker.py` against the same `AGENTS_HOME`
and drives a full write_inbox → run_once → wait_for_outbox round trip, to
catch drift between the two sides of the file contract (token format,
frontmatter parsing, directory layout).
