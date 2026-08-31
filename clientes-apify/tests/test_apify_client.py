import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apify_client  # noqa: E402


class ApifyClientTestCase(unittest.TestCase):
    def test_get_token_raises_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(apify_client.ApifyError):
                apify_client.get_token()

    def test_get_token_reads_env_var(self):
        with patch.dict("os.environ", {"APIFY_API_TOKEN": "secret"}, clear=True):
            self.assertEqual(apify_client.get_token(), "secret")

    def test_get_token_prefers_explicit_argument(self):
        with patch.dict("os.environ", {"APIFY_API_TOKEN": "from-env"}, clear=True):
            self.assertEqual(apify_client.get_token("from-arg"), "from-arg")

    def test_run_actor_sync_builds_request_and_parses_response(self):
        fake_response = io.BytesIO(json.dumps([{"title": "Panadería Uno"}]).encode())
        fake_response.status = 200

        class FakeContextManager:
            def __enter__(self_inner):
                return fake_response

            def __exit__(self_inner, *exc):
                return False

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeContextManager()

        with patch("apify_client.urllib.request.urlopen", side_effect=fake_urlopen):
            items = apify_client.run_actor_sync(
                "someone~some-actor", {"q": "panaderías"}, token="tok123"
            )

        self.assertEqual(items, [{"title": "Panadería Uno"}])
        self.assertIn("someone~some-actor", captured["url"])
        self.assertIn("token=tok123", captured["url"])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], {"q": "panaderías"})

    def test_run_actor_sync_wraps_http_error(self):
        def raise_http_error(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad token"}')
            )

        with patch("apify_client.urllib.request.urlopen", side_effect=raise_http_error):
            with self.assertRaises(apify_client.ApifyError) as ctx:
                apify_client.run_actor_sync("someone~some-actor", {}, token="bad")
        self.assertIn("401", str(ctx.exception))

    def test_run_actor_sync_wraps_connection_error(self):
        def raise_url_error(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        with patch("apify_client.urllib.request.urlopen", side_effect=raise_url_error):
            with self.assertRaises(apify_client.ApifyError):
                apify_client.run_actor_sync("someone~some-actor", {}, token="tok")


if __name__ == "__main__":
    unittest.main()
