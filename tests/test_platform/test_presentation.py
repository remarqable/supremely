"""The presentation seam: context declared at render_site, decided in one
place. Access decides who sees an object; presentation decides how."""

import pytest

from app.platform.theming import (
    PRESENTATION_CONTEXTS,
    SHELL_CONTEXTS,
    render_site,
)
from tests.conftest import login_as

ACME = 'http://acme.example.test'
SHELL_MARKER = b'aria-label="Community navigation"'


def test_context_vocabulary():
    assert PRESENTATION_CONTEXTS == ('publication', 'application', 'console',
                                     'error')
    # Policy lives in ONE mapping; flipping a surface is a data edit here.
    assert set(SHELL_CONTEXTS) <= set(PRESENTATION_CONTEXTS)
    # An error page is themed: the common case is a stale or mistyped link
    # arriving from outside, and it should land somewhere that still looks
    # like the site it was aimed at.
    assert SHELL_CONTEXTS['error'] is False


def test_unknown_context_is_loud(app, acme):
    with app.test_request_context(base_url=ACME), pytest.raises(ValueError):
        render_site(['single.html'], context_name='dashboard')


def test_publication_visitor_gets_shell(client, acme):
    # The shell serves everyone: a visitor browses the same community layout
    # members use, with gated content teased in place.
    page = client.get('/blog', base_url=ACME)
    assert page.status_code == 200
    assert SHELL_MARKER in page.data


def test_publication_member_gets_shell_per_policy(client, acme, user):
    login_as(client, user)
    page = client.get('/blog', base_url=ACME)
    assert SHELL_MARKER in page.data          # SHELL_CONTEXTS['publication']


def test_force_theme_front_page_for_members(client, acme, user):
    login_as(client, user)
    page = client.get('/', base_url=ACME)
    assert page.status_code == 200
    assert SHELL_MARKER not in page.data       # the public landing stays themed


def test_application_member_gets_community_templates(client, acme, user):
    login_as(client, user)
    page = client.get('/discussions/', base_url=ACME)
    assert SHELL_MARKER in page.data
