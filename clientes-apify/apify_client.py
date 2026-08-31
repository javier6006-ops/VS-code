"""Minimal Apify REST API client (stdlib only, no apify-client dependency).

Runs an Actor synchronously and returns its output dataset — the simplest
way to turn an Apify scraper into "give me a list of leads" without
separately polling a run's status.

Docs: https://docs.apify.com/api/v2#/reference/actors/run-actor-synchronously-and-get-dataset-items
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_BASE = "https://api.apify.com/v2"
DEFAULT_TIMEOUT_SECONDS = 120


class ApifyError(RuntimeError):
    """Raised when the Apify API call fails or the token is missing."""


def get_token(token: str | None = None) -> str:
    token = token or os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise ApifyError(
            "No Apify API token. Set APIFY_API_TOKEN or pass token= explicitly "
            "(find yours at https://console.apify.com/settings/integrations)."
        )
    return token


def run_actor_sync(
    actor_id: str,
    run_input: dict,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Run an Actor synchronously and return its output dataset as a list of dicts.

    `actor_id` is the Actor's "username~actor-name" slug (or its numeric id),
    e.g. "compass~crawler-google-places". `run_input` is the Actor's input
    JSON, shaped however that specific Actor expects it.
    """
    token = get_token(token)
    url = f"{API_BASE}/acts/{actor_id}/run-sync-get-dataset-items?token={token}"
    body = json.dumps(run_input).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApifyError(f"Apify Actor run failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApifyError(f"Could not reach Apify: {exc.reason}") from exc
