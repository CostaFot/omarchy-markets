"""A tiny in-process HTTP server for provider tests. No test touches the internet."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fixture_json(name):
    return json.loads(fixture(name).decode("utf-8"))


class FakeServer:
    """routes: path -> (status, headers, body) or callable(query, path) -> that tuple."""

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
                query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
                server.requests.append((parts.path, query))
                r = server.routes.get(parts.path)
                if r is None:
                    status, headers, body = 404, {}, b'{"error":"not found"}'
                elif callable(r):
                    status, headers, body = r(query, parts.path)
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
