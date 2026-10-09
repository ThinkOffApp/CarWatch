"""HTTP for requests that carry a secret token: redirects are refused, never followed.

urllib's default redirect handler builds the follow-up request with the
original headers (CPython drops only the body headers), so a token request
that gets a 30x hands the token to whatever host the Location names. For the
Home Assistant token that host has never passed `_is_private_ha`: a private HA
or the proxy in front of it that redirects to a public URL would leak a
long-lived token with full control of the house (Codex review of CarWatch #76).

So a token request answers a redirect with an HTTPError carrying the 30x code
and the target, and the caller reports it. Point the configured URL at the
final address instead of relying on a redirect.
"""
import urllib.error
import urllib.request


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"redirect to {newurl} refused: this request carries a token", headers, fp)


# build_opener drops its default HTTPRedirectHandler when given a subclass of it.
_OPENER = urllib.request.build_opener(_RefuseRedirect)


def urlopen(req, timeout: float):
    """urllib.request.urlopen for token-bearing requests: same API, no redirects."""
    return _OPENER.open(req, timeout=timeout)
