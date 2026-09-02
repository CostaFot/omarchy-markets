import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
if BIN not in sys.path:
    sys.path.insert(0, BIN)

# No test reaches the internet. A test that forgets to point a provider at
# its fake server hits a closed local port and fails fast instead of
# quietly fetching live data (it happened once with Yahoo).
for _name in ("MARKETS_COINGECKO_URL", "MARKETS_YAHOO_URL", "MARKETS_FRANKFURTER_URL"):
    os.environ.setdefault(_name, "http://127.0.0.1:9")
