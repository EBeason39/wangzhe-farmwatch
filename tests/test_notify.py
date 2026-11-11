import unittest
from unittest.mock import patch
import urllib.request
from urllib.error import URLError

import farmwatch.integrations.push as notify
class NotifyProxyFallbackTests(unittest.TestCase):
    def test_local_proxy_refusal_retries_direct_request(self):
        request = urllib.request.Request(
            "https://example.com/api",
            data=b'{"ok":true}',
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        sentinel = object()
        opener = type("FakeOpener", (), {})()

        def open_direct(req, timeout):
            self.assertEqual(req.full_url, request.full_url)
            self.assertEqual(req.data, request.data)
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(timeout, notify.HTTP_TIMEOUT)
            return sentinel

        opener.open = open_direct
        with (
            patch.object(
                urllib.request,
                "urlopen",
                side_effect=URLError(ConnectionRefusedError()),
            ) as urlopen,
            patch.object(
                urllib.request,
                "build_opener",
                return_value=opener,
            ) as build_opener,
        ):
            result = notify._open_url_with_direct_fallback(
                request, notify.HTTP_TIMEOUT
            )

        self.assertIs(result, sentinel)
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(build_opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
