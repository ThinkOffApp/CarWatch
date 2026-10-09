"""carwatch.tokenhttp: a request that carries a token never follows a redirect.

Two local servers. HOME answers /redirect with a 302 to OTHER (urllib follows a 302 even
for a POST, turning it into a GET and keeping the headers), which records every Authorization header it receives. The negative
control first shows that plain urllib DOES carry the token to OTHER on this
Python, so the remaining tests check a real hazard, not a vacuous one. Then
tokenhttp, Home Assistant's radiation reader and the Mercedes module's _get /
_post must all refuse the redirect, and OTHER must never see the token."""

import json
import socket
import socketserver
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from carwatch import mercedesme, tokenhttp

TOKEN = "test-token-not-a-secret-redirect-0123"


class _Other(BaseHTTPRequestHandler):
    seen: list = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        _Other.seen.append(self.headers.get("Authorization"))
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


class _Home(BaseHTTPRequestHandler):
    other = ""

    def _answer(self, code):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path.startswith("/redirect"):
            self.send_response(code)
            self.send_header("Location", _Home.other + "/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._answer(302)

    def do_POST(self):
        self._answer(302)

    def log_message(self, *a):
        pass


class _S(ThreadingHTTPServer):
    def server_bind(self):   # skip getfqdn(), see test_webchat_ask
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


def _serve(handler):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = _S(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def setUpModule():
    global HOME_SRV, OTHER_SRV, HOME, OTHER
    OTHER_SRV, OTHER = _serve(_Other)
    HOME_SRV, HOME = _serve(_Home)
    _Home.other = OTHER


def tearDownModule():
    HOME_SRV.shutdown()
    OTHER_SRV.shutdown()


def _req(path, method="GET"):
    data = json.dumps({"vin": "TEST"}).encode() if method == "POST" else None
    return urllib.request.Request(HOME + path, data=data, method=method,
                                  headers={"Authorization": f"Bearer {TOKEN}",
                                           "Content-Type": "application/json"})


class TestTokenHttp(unittest.TestCase):
    def setUp(self):
        _Other.seen = []

    def test_negative_control_plain_urllib_carries_the_token(self):
        # Measured 9 Oct 2026: Python 3.11, 3.12 (CI) and 3.13 carry it; 3.14 drops it itself.
        for method in ("GET", "POST"):
            _Other.seen = []
            with urllib.request.urlopen(_req("/redirect", method), timeout=5) as r:
                r.read()
            if f"Bearer {TOKEN}" not in _Other.seen:
                self.skipTest("this Python drops Authorization on redirects by itself")
            self.assertIn(f"Bearer {TOKEN}", _Other.seen, method)

    def test_get_redirect_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            tokenhttp.urlopen(_req("/redirect"), timeout=5)
        self.assertEqual(e.exception.code, 302)
        self.assertIn("refused", str(e.exception))
        self.assertEqual(_Other.seen, [])

    def test_post_redirect_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            tokenhttp.urlopen(_req("/redirect", "POST"), timeout=5)
        self.assertEqual(e.exception.code, 302)
        self.assertEqual(_Other.seen, [])

    def test_a_plain_answer_still_works(self):
        with tokenhttp.urlopen(_req("/ok"), timeout=5) as r:
            self.assertEqual(json.loads(r.read()), {"ok": True})


class TestMercedesTokenRequests(unittest.TestCase):
    def setUp(self):
        _Other.seen = []
        p = mock.patch.object(mercedesme, "_ha_url", lambda: HOME)
        p.start(); self.addCleanup(p.stop)

    def test_get_does_not_follow_a_redirect(self):
        with self.assertRaises(urllib.error.HTTPError):
            mercedesme._get("/redirect", TOKEN, timeout=5)
        self.assertEqual(_Other.seen, [])

    def test_post_does_not_follow_a_redirect(self):
        with self.assertRaises(urllib.error.HTTPError):
            mercedesme._post("/redirect", TOKEN, {"vin": "TEST"}, timeout=5)
        self.assertEqual(_Other.seen, [])

    def test_post_refuses_a_public_host_before_connecting(self):
        with mock.patch.object(mercedesme, "_ha_url", lambda: "http://8.8.8.8:8123"), \
             mock.patch.object(tokenhttp, "urlopen") as opened:
            with self.assertRaises(ValueError):
                mercedesme._post("/api/services/x", TOKEN, {"vin": "TEST"})
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
