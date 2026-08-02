"""HTTP transport - the only module in the repo that touches the network."""
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from fli.core.config import (BREAKER_THRESHOLD, HTTP_BACKOFF_S, HTTP_RETRIES,
                             HTTP_TIMEOUT_S)

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_sleep = time.sleep

_consecutive_failures: dict[str, int] = {}


def reset_breaker() -> None:
    """Clear breaker state (tests, and long-lived callers between runs)."""
    _consecutive_failures.clear()


class FetchError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] == 0:
        if os.path.exists("/etc/ssl/cert.pem"):
            ctx.load_verify_locations("/etc/ssl/cert.pem")
        else:
            import certifi
            ctx.load_verify_locations(certifi.where())
    return ctx


_CTX = None


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[str, str]:
    """GET a URL. """
    global _CTX
    if _CTX is None:
        _CTX = _ssl_context()
    host = urllib.parse.urlsplit(url).netloc
    if _consecutive_failures.get(host, 0) >= BREAKER_THRESHOLD:
        raise FetchError(
            f"circuit open for {host} after "
            f"{_consecutive_failures[host]} consecutive transport failures "
            f"this run")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last_err: Exception | None = None
    for attempt in range(HTTP_RETRIES + 1):
        if attempt:
            _sleep(HTTP_BACKOFF_S * 2 ** (attempt - 1))
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S,
                                        context=_CTX) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                last_err = e
                continue
            raise FetchError(str(e)) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            continue
        _consecutive_failures.pop(host, None)
        try:
            return raw.decode(charset), f"decode={charset};strict"
        except (UnicodeDecodeError, LookupError):
            return (raw.decode("utf-8", errors="replace"),
                    f"decode={charset}!failed;utf-8;replace")
    n = _consecutive_failures[host] = _consecutive_failures.get(host, 0) + 1
    note = f"; circuit opens for {host}" if n >= BREAKER_THRESHOLD else ""
    raise FetchError(
        f"{last_err} (after {HTTP_RETRIES + 1} attempts){note}") from last_err


RENDER_PROXY = "https://r.jina.ai/"


def http_get_rendered(url: str) -> str:
    """GET a page through the rendering proxy; returns readable TEXT, not HTML.
    GET a page through the rendering proxy; returns readable TEXT, not HTML."""
    body, _ = http_get(RENDER_PROXY + url)
    return body
