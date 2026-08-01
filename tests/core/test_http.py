"""Retry, backoff and circuit-breaker behavior of the HTTP chokepoint.

Every test patches urlopen inside fli.core.http — no network. The backoff
sleep is silenced via the module's `_sleep` alias, and breaker state is reset
around every test because it is process-scoped by design.
"""
import io
import unittest
import urllib.error
from unittest import mock

from fli.core import http as h
from fli.core.config import BREAKER_THRESHOLD, HTTP_RETRIES


def _ok_response(body: bytes = b"hello") -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status = 200
    resp.read.return_value = body
    resp.headers.get_content_charset.return_value = "utf-8"
    cm = mock.MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://feed.test/x", code, "boom",
                                  None, io.BytesIO(b""))


class HttpRetryTests(unittest.TestCase):
    def setUp(self):
        h.reset_breaker()
        h._CTX = object()                       # skip ssl context creation
        self._sleeps: list[float] = []
        p = mock.patch.object(h, "_sleep", self._sleeps.append)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(h.reset_breaker)

    def test_transient_5xx_is_retried_then_succeeds(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_http_error(500), _ok_response()]) as u:
            body, note = h.http_get("http://feed.test/rss")
        self.assertEqual(body, "hello")
        self.assertEqual(u.call_count, 2)
        self.assertEqual(self._sleeps, [1.0])   # first backoff step

    def test_backoff_doubles_per_attempt(self):
        errs = [_http_error(503)] * (HTTP_RETRIES + 1)
        with mock.patch("urllib.request.urlopen", side_effect=errs):
            with self.assertRaises(h.FetchError) as cm:
                h.http_get("http://feed.test/rss")
        self.assertEqual(self._sleeps, [1.0, 2.0])
        self.assertIn(f"after {HTTP_RETRIES + 1} attempts", str(cm.exception))

    def test_404_is_not_retried_and_message_format_is_stable(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(404)) as u:
            with self.assertRaises(h.FetchError) as cm:
                h.http_get("http://feed.test/gone")
        self.assertEqual(u.call_count, 1)
        # callers write str(e) into fetch_log.detail — keep the old wording
        self.assertIn("HTTP Error 404", str(cm.exception))

    def test_429_is_not_retried(self):
        """X rate windows are 15 minutes; a 2s backoff would just burn time."""
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(429)) as u:
            with self.assertRaises(h.FetchError):
                h.http_get("http://api.x.test/posts")
        self.assertEqual(u.call_count, 1)
        self.assertEqual(self._sleeps, [])

    def test_connection_error_is_retried(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[urllib.error.URLError("reset"),
                                     _ok_response(b"ok")]) as u:
            body, _ = h.http_get("http://feed.test/rss")
        self.assertEqual(body, "ok")
        self.assertEqual(u.call_count, 2)


class CircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        h.reset_breaker()
        h._CTX = object()
        p = mock.patch.object(h, "_sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(h.reset_breaker)

    def _fail_once(self, url: str):
        errs = [urllib.error.URLError("down")] * (HTTP_RETRIES + 1)
        with mock.patch("urllib.request.urlopen", side_effect=errs):
            with self.assertRaises(h.FetchError):
                h.http_get(url)

    def test_breaker_opens_after_threshold_and_fails_fast(self):
        for _ in range(BREAKER_THRESHOLD):
            self._fail_once("http://dead.test/page")
        # circuit open: no network call is made at all
        with mock.patch("urllib.request.urlopen") as u:
            with self.assertRaises(h.FetchError) as cm:
                h.http_get("http://dead.test/other-page")
        self.assertEqual(u.call_count, 0)
        self.assertIn("circuit open for dead.test", str(cm.exception))

    def test_breaker_is_per_host(self):
        for _ in range(BREAKER_THRESHOLD):
            self._fail_once("http://dead.test/page")
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response()) as u:
            body, _ = h.http_get("http://alive.test/rss")
        self.assertEqual(body, "hello")
        self.assertEqual(u.call_count, 1)

    def test_success_resets_the_count(self):
        for _ in range(BREAKER_THRESHOLD - 1):
            self._fail_once("http://flaky.test/page")
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response()):
            h.http_get("http://flaky.test/page")
        # count is back to zero: threshold-1 more failures still don't open it
        for _ in range(BREAKER_THRESHOLD - 1):
            self._fail_once("http://flaky.test/page")
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response()) as u:
            h.http_get("http://flaky.test/page")
        self.assertEqual(u.call_count, 1)

    def test_4xx_does_not_count_toward_the_breaker(self):
        for _ in range(BREAKER_THRESHOLD + 1):
            with mock.patch("urllib.request.urlopen",
                            side_effect=_http_error(404)):
                with self.assertRaises(h.FetchError):
                    h.http_get("http://misconfigured.test/gone")
        # host still reachable: a real request would go through
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response()) as u:
            h.http_get("http://misconfigured.test/ok")
        self.assertEqual(u.call_count, 1)


if __name__ == "__main__":
    unittest.main()
