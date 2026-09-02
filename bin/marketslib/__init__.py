"""marketslib — the data core of the omarchy-markets plugin.

Pure Python 3 standard library. The QML side never talks to the network
or touches state files; it runs `bin/markets <command>` and renders the
one-line JSON document that comes back. See cli.py for the contract.
"""

import json
import os


def plugin_version():
    """The manifest's version is the single source of truth."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    try:
        with open(os.path.join(root, "manifest.json"), "r", encoding="utf-8") as f:
            return str(json.load(f).get("version", "dev"))
    except (OSError, ValueError):
        return "dev"


USER_AGENT = "costafot.markets/" + plugin_version()
