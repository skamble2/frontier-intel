"""HTTP transport - the only module in the repo that touches the network."""
import os
import ssl
import urllib.error
import urllib.request

from fli.core.config import HTTP_TIMEOUT_S

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class FetchError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    # some Python builds ship without CA certs; use the system bundle or
    # certifi as fallback — verification is never disabled
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
    """GET a URL. Returns (body, decode_note); raises FetchError on failure.

    Decoding honors the server-declared charset and tries strict first;
    only on failure does it fall back to utf-8/replace, and the note says so.

    `headers` adds request headers (the X API needs an Authorization bearer).
    Nothing here logs them — a token must never reach fetch_log.detail.
    """
    global _CTX
    if _CTX is None:
        _CTX = _ssl_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S, context=_CTX) as resp:
            if resp.status != 200:
                raise FetchError(f"HTTP {resp.status}")
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(str(e)) from e
    try:
        return raw.decode(charset), f"decode={charset};strict"
    except (UnicodeDecodeError, LookupError):
        return (raw.decode("utf-8", errors="replace"),
                f"decode={charset}!failed;utf-8;replace")
