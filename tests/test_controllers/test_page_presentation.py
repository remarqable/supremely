"""Per-page presentation: a standalone page declares where it appears.

'site' (the default) renders through the theme — the marketing header and
footer; 'community' renders inside the app-owned shell. Presentation is
never authorization: visibility gates the body either way.
"""

from flask import g

from app.models import Content
from app.platform.errors import ValidationError
from tests.conftest import login_as

ACME = 'http://acme.example.test'
COMMUNITY_MARKER = b'aria-label="Community navigation"'


def make_page(app, acme, slug, presentation=None, visibility='public'):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title=slug.title(), slug=slug,
                       body=f'Body of {slug}.', org_id=acme.id,
                       visibility=visibility, fields={}, tags=[])
        if presentation is not None:
            page.presentation = presentation
        page.save()
        page.publish()


def test_pages_render_themed_by_default(app, client, acme, user):
    make_page(app, acme, 'story')
    anon = app.test_client()
    response = anon.get('/story', base_url=ACME)
    assert response.status_code == 200
    assert b'Body of story.' in response.data
    assert COMMUNITY_MARKER not in response.data
    # Members get the same public look: presentation follows the object,
    # not the viewer.
    login_as(client, user)
    response = client.get('/story', base_url=ACME)
    assert response.status_code == 200
    assert COMMUNITY_MARKER not in response.data


def test_a_community_page_renders_in_the_shell(app, client, acme):
    make_page(app, acme, 'guidelines', presentation='community')
    anon = app.test_client()
    response = anon.get('/guidelines', base_url=ACME)
    assert response.status_code == 200
    assert b'Body of guidelines.' in response.data
    assert COMMUNITY_MARKER in response.data


def test_a_members_only_site_page_still_gates(app, client, acme, user):
    make_page(app, acme, 'insiders', visibility='members')
    anon = app.test_client()
    response = anon.get('/insiders', base_url=ACME)
    # Teasing is on by default: the gate page renders, never the body.
    assert response.status_code == 200
    assert b'Body of insiders.' not in response.data
    # A member reads it, themed.
    login_as(client, user)
    response = client.get('/insiders', base_url=ACME)
    assert response.status_code == 200
    assert b'Body of insiders.' in response.data
    assert COMMUNITY_MARKER not in response.data


def test_presentation_value_is_validated(app, acme):
    import pytest
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='Bad', slug='bad', org_id=acme.id,
                       presentation='popup', fields={}, tags=[])
        with pytest.raises(ValidationError):
            page.save()


def test_a_site_presented_type_archive_renders_themed(app, client, acme):
    """team_member declares presentation='site': its archive and singles get
    the theme, not the shell — same seam as pages, declared on the type."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        member = Content(type='team_member', title='Jo Doe', slug='jo-doe',
                         body='Bio of Jo.', org_id=acme.id,
                         fields={'role': 'Founder'}, tags=[])
        member.save()
        member.publish()
    anon = app.test_client()
    archive = anon.get('/team', base_url=ACME)
    assert archive.status_code == 200
    assert b'Jo Doe' in archive.data
    assert b'Founder' in archive.data
    assert COMMUNITY_MARKER not in archive.data
    single = anon.get('/team/jo-doe', base_url=ACME)
    assert single.status_code == 200
    assert COMMUNITY_MARKER not in single.data


def test_a_community_type_archive_stays_in_the_shell(app, client, acme):
    anon = app.test_client()
    archive = anon.get('/blog', base_url=ACME)
    assert archive.status_code == 200
    assert COMMUNITY_MARKER in archive.data
