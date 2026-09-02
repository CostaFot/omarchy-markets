"""Where the helper keeps its files, and how it writes them.

State dir: ${XDG_STATE_HOME:-~/.local/state}/omarchy/costafot.markets/
(override with MARKETS_STATE_DIR — tests point it at a temp dir).
Every write is atomic (temp file + os.replace) and 0600, so a crash
mid-write never leaves a half-written watchlist behind.
"""

import json
import os
import tempfile


def state_dir():
    d = os.environ.get("MARKETS_STATE_DIR")
    if not d:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        d = os.path.join(base, "omarchy", "costafot.markets")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def read_json(path, default=None):
    """Parsed JSON, or `default` when the file does not exist.

    A corrupt file raises ValueError (json.JSONDecodeError) — callers decide
    whether that means "start over" (see store.Watchlist) or "ignore" (caches).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json_atomic(path, data):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
