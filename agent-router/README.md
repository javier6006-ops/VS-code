# Agent Router

Classifies an incoming plain-text message and dispatches it to exactly one
downstream agent's inbox, then waits for its reply — the "pass-through"
component in front of the actual worker agents (e.g. `email`, `company`,
`ecommerce`) and the sibling [`agent-dashboard`](../agent-dashboard) that
watches them.

This module never talks to Telegram (or any channel) itself — it takes a
message plus who sent it and where from, and returns the reply text(s).
Wire it up to a real channel by having that bot call `handle_message(...)`
on every incoming update and send each returned string back on the same
channel.

## Layout it expects (same `~/agents` tree as agent-dashboard)

```
~/agents/
  router/
    unclassified.log         # one JSON line per message with no matching agent
  <agent-name>/
    inbox/
      <token>.md              # written by the router, read by the agent
    outbox/
      <token>.md              # written by the agent, read by the router
```

Set `AGENTS_HOME` to point somewhere other than `~/agents` if needed.

### Inbox file format

```
---
from_user: alejandro
channel: telegram
timestamp: 2026-08-31T12:00:00Z
---

<original message text>
```

### Outbox file format

Plain text (or markdown) — whatever the agent wants forwarded back to the
user, written to `outbox/<same-token>.md`.

## Dispatch table

| Message contains | Agent |
|---|---|
| email, correo, bandeja, inbox, redactar correo, responder | `email` |
| empresa, productos, precios internos, contratos, política, equipo | `company` |
| shopify, store, tienda, pedidos, inventario, competencia, reviews, reseñas | `ecommerce` |
| none of the above | replies "No tengo un agente para eso todavía. Lo registro." and logs it to `router/unclassified.log` |

Matching is a case-insensitive substring check per keyword — good enough
for routing, not full NLU.

## Rules encoded in the logic

- **One agent per turn, never a broadcast.** A message matching keywords
  from more than one agent is either split on "y"/"and" into sequential
  turns (each dispatched and awaited in order — e.g. *"revisa mi inbox y
  avísame de Shopify"* becomes two turns: `email`, then `ecommerce`), or,
  if it can't be cleanly split, answered with a clarifying question:
  *"¿Esto va para X o para Y?"*
- **Polling, not push.** After writing the inbox file, the router polls for
  `outbox/<token>.md` every `poll_interval` seconds (default 10) up to
  `timeout` seconds (default 300 / 5 min). If nothing shows up in time, it
  replies *"El agente `<name>` está tardando. Te aviso cuando conteste."*
  instead of blocking forever.
- **No opinions.** The router only classifies and relays; the reply text is
  always whatever the destination agent wrote to its outbox (or one of the
  three canned router messages above).

## Usage

```bash
python router.py dispatch --message "responde este correo" --from-user alejandro --channel telegram
python router.py dispatch --message "revisa mi inbox y avísame de Shopify" --from-user alejandro --channel telegram --timeout 300 --poll-interval 10
```

Or from Python, e.g. inside a Telegram bot's update handler:

```python
import router

replies = router.handle_message(update.text, update.from_user, "telegram")
for reply in replies:
    bot.send_message(update.chat_id, reply)
```

## Tests

```bash
python -m unittest discover -s tests
```
