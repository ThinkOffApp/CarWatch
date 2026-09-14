"""POST /ask through a real server: a tokened page posts /ask?t=... and must
reach the answer, not a 404 (claudemm, 14 Sep 2026, measured against VTA).
The server runs in a thread on a free port with a temp CARWATCH_STATE
holding the dash token; the brain is a canned function."""

import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock


class _QuietServer:
    """ThreadingHTTPServer without HTTPServer.server_bind's getfqdn(): that
    reverse lookup took 35 s on a Mac with a dead resolver, so the test's
    wall time depended on DNS instead of the code."""

    @classmethod
    def make(cls, webchat, addr):
        import socketserver

        class _S(webchat.ThreadingHTTPServer):
            def server_bind(self):
                socketserver.TCPServer.server_bind(self)
                self.server_name = "localhost"
                self.server_port = self.server_address[1]
        return _S(addr, webchat.Handler)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class TestAskRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls._env = os.environ.get("CARWATCH_STATE")
        os.environ["CARWATCH_STATE"] = cls.tmp
        from carwatch import webchat
        cls.webchat = webchat
        cls.port = _free_port()
        cls.srv = _QuietServer.make(webchat, ("127.0.0.1", cls.port))
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        if cls._env is None:
            os.environ.pop("CARWATCH_STATE", None)
        else:
            os.environ["CARWATCH_STATE"] = cls._env

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    def _only_the_token_decides(self):
        """Pin the two things the handler reads from the box: the token
        (Handler._token reads Handler.TOKEN_PATH, the real ~/.carwatch, not
        CARWATCH_STATE) and the own-peer trust (the test client is the box's
        loopback peer, which _authorised() would accept without a token)."""
        p1 = mock.patch.object(self.webchat.Handler, "_token", classmethod(lambda cls: "right-token"))
        p2 = mock.patch.object(self.webchat.Handler, "_peer_is_owner", lambda self: False)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_tokened_ask_reaches_the_answer(self):
        self._only_the_token_decides()
        with mock.patch.object(self.webchat, "answer", lambda q, manual=True: "canned: " + q):
            code, j = self._post("/ask?t=right-token", {"q": "hello", "manual": False})
        self.assertEqual(code, 200, j)
        self.assertEqual(j.get("answer"), "canned: hello")

    def test_wrong_token_is_refused(self):
        self._only_the_token_decides()
        with mock.patch.object(self.webchat, "answer", lambda q, manual=True: "never"):
            code, j = self._post("/ask?t=wrong-token", {"q": "hello"})
            bare, _ = self._post("/ask", {"q": "hello"})
        self.assertIn(code, (401, 403), j)
        self.assertIn(bare, (401, 403))


if __name__ == "__main__":
    unittest.main()
