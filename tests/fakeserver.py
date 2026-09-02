"""A tiny in-process HTTP server for provider tests. No test touches the internet."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fixture_json(name):
    return json.loads(fixture(name).decode("utf-8"))


class FakeServer:
    """routes: path -> (status, headers, body) or callable(query, path) -> that tuple.
    Paths are matched decoded ("/v8/finance/chart/EURUSD=X"). Every request is
    recorded as (path, query, headers)."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self._server = None
        self._thread = None
        self.base_url = ""

    def route(self, path, status=200, body=b"", headers=None, json_body=None):
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        self.routes[path] = (status, headers or {}, body)

    def handler(self, path, fn):
        self.routes[path] = fn

    def hits(self, path=None):
        return [r for r in self.requests if path is None or r[0] == path]

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
                server.requests.append((path, query, {k.lower(): v for k, v in self.headers.items()}))
                r = server.routes.get(path)
                if r is None:
                    status, headers, body = 404, {}, b'{"error":"not found"}'
                elif callable(r):
                    status, headers, body = r(query, path)
                else:
                    status, headers, body = r
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def coingecko_routes(server):
    """The happy-path CoinGecko surface from the recorded fixtures."""
    server.route("/coins/markets", body=fixture("coingecko_markets.json"))
    server.route("/search", body=fixture("coingecko_search.json"))
    server.route("/coins/bitcoin/market_chart", body=fixture("coingecko_chart.json"))
    return server


def yahoo_routes(server):
    """The happy-path Yahoo surface from the recorded fixtures. Yahoo refuses
    requests without a real User-Agent, so the fake does too (429)."""
    # The seed watchlist's other symbols reuse the AAPL / EURUSD bodies so a
    # snapshot prices everything on first sight; only ZZZZQQ is unknown.
    charts = {
        "/v8/finance/chart/AAPL": "yahoo_chart_aapl.json",
        "/v8/finance/chart/MSFT": "yahoo_chart_aapl.json",
        "/v8/finance/chart/NVDA": "yahoo_chart_aapl.json",
        "/v8/finance/chart/HSBA.L": "yahoo_chart_hsba.json",
        "/v8/finance/chart/EURUSD=X": "yahoo_chart_eurusd.json",
        "/v8/finance/chart/GBPUSD=X": "yahoo_chart_eurusd.json",
        "/v8/finance/chart/USDJPY=X": "yahoo_chart_eurusd.json",
    }

    def guarded(name, status=200):
        def handler(query, path):
            ua = [r[2].get("user-agent", "") for r in server.requests][-1]
            if not ua.startswith("costafot.markets/"):
                return 429, {}, b"Too Many Requests"
            if path == "/v8/finance/chart/AAPL" and query.get("range") == "5y":
                return 200, {}, fixture("yahoo_chart_5y.json")
            return status, {}, fixture(name)
        return handler

    for path, name in charts.items():
        server.handler(path, guarded(name))
    server.handler("/v8/finance/chart/ZZZZQQ", guarded("yahoo_chart_404.json", status=404))
    server.handler("/v8/finance/spark", guarded("yahoo_spark.json"))
    server.handler("/v1/finance/search", guarded("yahoo_search.json"))
    return server
