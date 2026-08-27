"""Where a redirect taken from a request is allowed to go.

The trap these guard against is a value that passes the check and then gets
rewritten on the way out, so the address followed is not the address that was
inspected.
"""

import pytest

from app.platform.redirects import safe_next

FALLBACK = '/fallback'


@pytest.mark.parametrize('candidate', [
    '/\t/evil.example.com',      # a tab: the header serializer drops it,
    '/\n/evil.example.com',      # leaving //evil.example.com
    '/\r\n/evil.example.com',
    '/\x00/evil.example.com',
    '/\x7f/evil.example.com',
    ' /dashboard',               # leading space, same trick
    '\t//evil.example.com',
    '//evil.example.com',        # scheme-relative
    '////evil.example.com',
    '/\\evil.example.com',       # browsers read a backslash as a slash
    'https://evil.example.com',
    'javascript:alert(1)',
    'evil.example.com',          # no leading slash: not this site
])
def test_a_redirect_that_leaves_this_site_is_refused(candidate):
    assert safe_next(candidate, FALLBACK) == FALLBACK


@pytest.mark.parametrize('candidate', [
    '/dashboard',
    '/blog?tag=security',
    '/a/b/c#section',
])
def test_a_site_relative_path_is_kept(candidate):
    assert safe_next(candidate, FALLBACK) == candidate


@pytest.mark.parametrize('candidate', [None, ''])
def test_nothing_asked_for_means_the_default(candidate):
    assert safe_next(candidate, FALLBACK) == FALLBACK


def test_current_target_keeps_a_path_encoded(app):
    """request.path comes back percent-decoded, so a page whose address holds
    an encoded character would produce a `next` that safe_next then refuses."""
    from app.platform.redirects import current_target

    with app.test_request_context('/blog/tag/machine%20learning'):
        target = current_target()

    assert target == '/blog/tag/machine%20learning'
    assert safe_next(target, FALLBACK) == target


def test_current_target_keeps_the_query_string(app):
    from app.platform.redirects import current_target

    with app.test_request_context('/discussions/?q=two%20words&sort=new'):
        target = current_target()

    assert target == '/discussions/?q=two%20words&sort=new'
    assert safe_next(target, FALLBACK) == target
