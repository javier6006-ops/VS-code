import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import leads  # noqa: E402


class LeadsTestCase(unittest.TestCase):
    def test_normalize_lead_maps_known_aliases(self):
        item = {
            "title": "Panadería Uno",
            "website": "https://panaderiauno.cl",
            "phoneUnformatted": "+56912345678",
            "email": ["contacto@panaderiauno.cl"],
            "address": "Av. Siempre Viva 123",
        }
        lead = leads.normalize_lead(item)
        self.assertEqual(lead["name"], "Panadería Uno")
        self.assertEqual(lead["website"], "https://panaderiauno.cl")
        self.assertEqual(lead["phone"], "+56912345678")
        self.assertEqual(lead["email"], "contacto@panaderiauno.cl")
        self.assertEqual(lead["address"], "Av. Siempre Viva 123")
        self.assertEqual(lead["raw"], item)

    def test_normalize_lead_missing_fields_are_none(self):
        lead = leads.normalize_lead({"title": "Solo Nombre"})
        self.assertEqual(lead["name"], "Solo Nombre")
        self.assertIsNone(lead["email"])
        self.assertIsNone(lead["website"])

    def test_dedupe_leads_drops_repeats_and_missing_key(self):
        items = [
            {"email": "a@x.com", "name": "A"},
            {"email": "a@x.com", "name": "A duplicate"},
            {"email": None, "name": "Sin email"},
            {"email": "b@x.com", "name": "B"},
        ]
        deduped = leads.dedupe_leads(items, key="email")
        self.assertEqual([l["name"] for l in deduped], ["A", "B"])

    def test_save_leads_csv_writes_expected_columns(self):
        rows = leads.normalize_leads(
            [{"title": "A", "email": "a@x.com"}, {"title": "B", "email": "b@x.com"}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            leads.save_leads_csv(rows, path)
            with path.open(newline="", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
        self.assertEqual(len(reader), 2)
        self.assertEqual(reader[0]["name"], "A")
        self.assertEqual(reader[0]["email"], "a@x.com")
        self.assertNotIn("raw", reader[0])

    def test_save_leads_json_round_trips(self):
        rows = leads.normalize_leads([{"title": "A", "email": "a@x.com"}])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            leads.save_leads_json(rows, path)
            loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded[0]["name"], "A")
        self.assertEqual(loaded[0]["raw"]["title"], "A")


if __name__ == "__main__":
    unittest.main()
