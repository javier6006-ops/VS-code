"""Stub handler for the "email" worker agent.

This proves the router -> inbox -> worker -> outbox -> router pipeline
end-to-end; it does not touch a real mailbox. Wiring up a real one (Gmail
API OAuth, IMAP/SMTP, or similar) needs real credentials for whatever
provider gets chosen — that's separate follow-up work this stub can't do
on its own. Swap this function's body for real logic once that's ready;
the worker/router contract around it does not need to change.
"""
from __future__ import annotations

STUB_NOTICE = "no tengo acceso real a tu correo todavía"


def handle(message: str, metadata: dict) -> str:
    from_user = metadata.get("from_user", "alguien")
    lowered = message.lower()

    if "redactar" in lowered or "escrib" in lowered:
        return f"Ok {from_user}, {STUB_NOTICE} para redactarlo — lo dejo anotado."
    if "responder" in lowered:
        return f"Ok {from_user}, {STUB_NOTICE} para responder — lo dejo anotado."
    return f"Recibido, {from_user}. {STUB_NOTICE.capitalize()}; esto quedó registrado."
