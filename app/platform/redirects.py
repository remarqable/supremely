"""Where a redirect taken from user input is allowed to go."""

import re
from urllib.parse import quote

# Control characters, space and backslash. A browser or a header serializer
# rewrites these, which is the whole problem: the value that was checked stops
# being the value that is followed.
_REWRITABLE = re.compile(r'[\x00-\x20\x7f\\]')


def safe_next(candidate: str | None, default: str) -> str:
    """A site-relative path taken from a request, or `default`.

    Anything that leaves this app's own host is refused, and so is anything
    that would be rewritten on the way out: `/<tab>/evil.example` passes a
    naive startswith('/') check, and then reaches the Location header as
    `//evil.example`, which is a different site.

    The value checked is the value returned: no normalised copy is
    inspected and then discarded, which is how the tab got through before.

    Build a candidate with current_target(), not request.path, which
    Werkzeug hands back percent-decoded.
    """
    if not candidate or _REWRITABLE.search(candidate):
        return default
    # Starting with a single slash is what makes a scheme or a host
    # impossible: neither can appear before the first slash.
    if not candidate.startswith('/') or candidate.startswith('//'):
        return default
    return candidate


def current_target() -> str:
    """This request's path and query, encoded, for a `next` field.

    request.path is percent-decoded, so a page whose address contains an
    encoded character (a tag with a space, say) comes back with a literal
    one, which safe_next then refuses.
    """
    from flask import request
    target = quote(request.path)
    if request.query_string:
        target += '?' + request.query_string.decode('latin-1')
    return target
