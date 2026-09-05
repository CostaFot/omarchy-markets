"""Bounded HTTP GET with the Windows extension's 429 back-off.

Port of Helpers/HttpRetry.cs (max 3 attempts, honour Retry-After, else
1s/2s, give up if the wait would exceed 8s) on top of the capped,
redirect-refusing reader from umarchy's bin/umami-api: the body is read
one byte at a time so both the byte cap and the wall-clock deadline are
enforced between reads, no matter how slowly a server drips.

Never log a URL with its query string — API keys ride there. redact()
strips the known key parameters for MARKETS_DEBUG output.
"""

import email.utils
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT

MAX_ATTEMPTS = 3
MAX_DELAY_SECONDS = 8.0
KEY_PARAMS = ("apikey", "token", "key", "x-cg-demo-api-key")

# Process-wide, like RateLimitSignal: a free-tier limit is key-wide, not per symbol.
RATE_LIMITED = False
# True once any request in this run came back 2xx; with RATE_LIMITED it is
# what the repository's persisted rate-limit latch keys off.
SUCCEEDED = False


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def max_response_bytes():
    return int(_env_float("MARKETS_MAX_RESPONSE_BYTES", 1024 * 1024))


def socket_timeout():
    return _env_float("MARKETS_SOCKET_TIMEOUT", 10)


def total_timeout():
    return _env_float("MARKETS_TOTAL_TIMEOUT", 20)


def total_budget():
    """Wall-clock budget for the whole process (cli.main arms it as an
    alarm); 0 disables. The store passes the same number in the
    environment and kills the helper a little after it, so the JSON
    answer is the normal path and the kill is the fallback."""
    return max(0.0, _env_float("MARKETS_TOTAL_BUDGET", 90))


def backoff_scale():
    """Tests set MARKETS_BACKOFF_SCALE=0 so the 1s/2s waits vanish."""
    return _env_float("MARKETS_BACKOFF_SCALE", 1)


class FetchError(Exception):
    """One failed request. `code` is the envelope's error code vocabulary."""

    def __init__(self, code, message, status=None, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after

    def to_dict(self):
        d = {"code": self.code, "message": self.message}
        if self.status is not None:
            d["status"] = self.status
        if self.retry_after is not None:
            d["retry_after"] = self.retry_after
        return d


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """A redirect would resend our headers (and a key) wherever it points. None are followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirectHandler)


def redact(url):
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept = []
    for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        kept.append((k, "***" if k.lower() in KEY_PARAMS else v))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept)))


def debug(message):
    if os.environ.get("MARKETS_DEBUG"):
        print("markets: " + message, file=sys.stderr)


def read_capped(resp, max_bytes, deadline):
    chunks = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(f"request exceeded {total_timeout():.0f}s")
        chunk = resp.read(1)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response exceeded {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_after_seconds(headers):
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        secs = float(value)
        return secs if secs > 0 else None
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    wait = when.timestamp() - time.time()
    return wait if wait > 0 else None


def get(url, headers=None, tag=""):
    """Return (status, body_bytes). Retries only on 429; any other status is
    returned as-is for the caller to interpret. Raises FetchError for
    network failures, timeouts and the byte cap."""
    global RATE_LIMITED, SUCCEEDED
    req_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    attempt = 0
    while True:
        attempt += 1
        deadline = time.monotonic() + total_timeout()
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        debug(f"{tag} GET {redact(url)} (attempt {attempt})")
        try:
            try:
                with _opener.open(req, timeout=socket_timeout()) as resp:
                    status = resp.status
                    body = read_capped(resp, max_response_bytes(), deadline)
                    resp_headers = resp.headers
            except urllib.error.HTTPError as e:
                status = e.code
                resp_headers = e.headers
                try:
                    body = read_capped(e, max_response_bytes(), deadline)
                except (ValueError, TimeoutError):
                    body = b""
                finally:
                    e.close()
        except ValueError as e:
            raise FetchError("too_large", str(e)) from e
        except TimeoutError as e:
            raise FetchError("network", f"timed out: {e}") from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            reason = getattr(e, "reason", e)
            raise FetchError("network", f"network error: {reason}") from e

        if status != 429:
            if 200 <= status < 300:
                RATE_LIMITED = False
                SUCCEEDED = True
            return status, body

        delay = _retry_after_seconds(resp_headers)
        if delay is None:
            delay = float(1 << (attempt - 1))
        if attempt >= MAX_ATTEMPTS or delay > MAX_DELAY_SECONDS:
            debug(f"{tag} 429 — giving up after {attempt} attempt(s), next wait {delay:.0f}s")
            RATE_LIMITED = True
            return 429, body
        debug(f"{tag} 429 — backing off {delay:.0f}s before retry {attempt + 1}/{MAX_ATTEMPTS}")
        time.sleep(delay * backoff_scale())


def _error_message(body, status):
    """A provider's own error text when it sent one, else a plain status line."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = None
    msg = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            st = err.get("status")
            if isinstance(st, dict):
                msg = st.get("error_message")
            msg = msg or err.get("message")
        elif isinstance(err, str):
            msg = err
        msg = msg or parsed.get("message")
    return str(msg) if msg else f"HTTP {status}"


def get_json(url, headers=None, tag=""):
    """GET and parse JSON; raises FetchError on any non-2xx status, a 429 that
    survived the retries, or an unparseable body."""
    status, body = get(url, headers=headers, tag=tag)
    if status == 429:
        raise FetchError("rate_limited", "rate-limited by the provider", status=429)
    if not 200 <= status < 300:
        raise FetchError("http", _error_message(body, status), status=status)
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FetchError("bad_response", f"invalid JSON from provider: {e}") from e
