"""#12: the dashboard token rides in the URL, so headers matter.

Two findings, both about /api/status, which returns live car facts, the Pi's
process list and agent journal lines:

1. It carried `Access-Control-Allow-Origin: *`. On home wifi the dashboard
   authorises by NETWORK (_peer_is_owner), not by token - ambient authority.
   Ambient authority plus wildcard CORS means any web page open on a phone on
   that wifi could fetch this endpoint and read the reply cross-origin.
2. Nothing set Referrer-Policy, so a `?t=<token>` URL leaked the credential in
   the Referer of every outbound link.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "carwatch", "webchat.py")


class DashboardHeaders(unittest.TestCase):

    def setUp(self):
        with open(SRC) as f:
            self.src = f.read()

    def test_no_wildcard_cors_anywhere(self):
        # A comment naming the header is fine; actually sending it is not.
        sent = re.findall(r'send_header\(\s*["\']Access-Control-Allow-Origin["\']',
                          self.src)
        self.assertEqual(sent, [], "the dashboard is same-origin and must not "
                                   "advertise CORS on token-gated car data")

    def test_referrer_policy_is_sent(self):
        self.assertIn('send_header("Referrer-Policy", "no-referrer")', self.src,
                      "the token travels as ?t=, so it would leak in Referer")

    def test_status_endpoint_still_returns_the_dashboard_payload(self):
        # Removing a header must not have disturbed the response body.
        for key in ('"device"', '"listening"', '"facts"', '"top"', '"journal"'):
            self.assertIn(key, self.src)

    def test_ambient_auth_still_exists_so_the_cors_fix_matters(self):
        # If this ever stops being true the finding changes shape, so assert
        # the premise rather than leaving it in a comment.
        self.assertIn("_pi_on_home_wifi()", self.src)
        self.assertIn("def _peer_is_owner", self.src)


if __name__ == "__main__":
    unittest.main()
