"""Turns raw Apify dataset items (shape varies per Actor) into a stable,
deduplicated list of leads for client acquisition.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

# Common field names across popular scraping Actors (Google Maps, LinkedIn,
# generic website/contact scrapers, ...). Extend as needed per Actor.
FIELD_ALIASES: dict[str, list[str]] = {
    "name": ["title", "name", "companyName", "fullName"],
    "website": ["website", "url", "webSite"],
    "phone": ["phone", "phoneNumber", "phoneUnformatted"],
    "email": ["email", "emails"],
    "address": ["address", "location", "fullAddress"],
}


def _first_present(item: dict, aliases: list[str]):
    for key in aliases:
        if key in item and item[key]:
            value = item[key]
            return value[0] if isinstance(value, list) and value else value
    return None


def normalize_lead(item: dict) -> dict:
    """Map one raw Apify item onto the common lead shape, keeping the raw
    item alongside it under "raw" so nothing Actor-specific is lost."""
    lead = {field: _first_present(item, aliases) for field, aliases in FIELD_ALIASES.items()}
    lead["raw"] = item
    return lead


def normalize_leads(items: list[dict]) -> list[dict]:
    return [normalize_lead(item) for item in items]


def dedupe_leads(leads: list[dict], key: str = "email") -> list[dict]:
    """Drop leads with a duplicate (or missing) value for `key`, keeping the
    first occurrence."""
    seen = set()
    deduped = []
    for lead in leads:
        value = lead.get(key)
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(lead)
    return deduped


def save_leads_json(leads: list[dict], path: Path) -> None:
    path.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")


def save_leads_csv(leads: list[dict], path: Path) -> None:
    fieldnames = [f for f in FIELD_ALIASES if f != "raw"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for lead in leads:
            writer.writerow({field: lead.get(field, "") or "" for field in fieldnames})
