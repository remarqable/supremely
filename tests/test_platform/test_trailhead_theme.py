"""The Trailhead example theme: the tutorial's worked example, kept honest.

Mirrors the tutorial's fictitious community: landing + articles + events
public; discussions members-only except the public Welcome group; joining
required to post or reply. All of that is object visibility + server
enforcement — the theme is presentation only.
"""

from flask import g

from app.platform.theming import AVAILABLE_THEMES
from tests.conftest import login_as

ACME = 'http://acme.example.test'
TRAIL_MARKER = b'trail-hero'


def use_trailhead(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.theme = 'trailhead'
        acme.save()


def test_theme_registers(app):
    with app.test_request_context(base_url=ACME):
        assert 'trailhead' in AVAILABLE_THEMES
        assert AVAILABLE_THEMES['trailhead']['settings']['forest']['default']


def test_visitor_experience(app, client, acme):
    use_trailhead(app, acme)
    # Landing: themed hero.
    home = client.get('/', base_url=ACME)
    assert home.status_code == 200
    assert TRAIL_MARKER in home.data
    # Content surfaces use the community shell for everyone; the theme
    # styles the landing. Visitors browse the same layout members use.
    for path in ('/blog', '/events'):
        page = client.get(path, base_url=ACME)
        assert page.status_code == 200, path
        assert b'trailhead' not in page.data, path   # shell, not theme chrome
    # Members-only groups are teased with a lock and their posts stay
    # gated — server behavior, no theme code.
    disc = client.get('/discussions/', base_url=ACME)
    assert disc.status_code == 200
    assert b'General' in disc.data                   # teased by name
    assert b'Members only' in disc.data              # with a lock badge


def test_member_shell_is_not_themed(app, client, acme, user):
    use_trailhead(app, acme)
    login_as(client, user)
    page = client.get('/dashboard', base_url=ACME)
    assert page.status_code == 200
    assert TRAIL_MARKER not in page.data             # app surface standardized


def test_anonymous_cannot_participate(app, client, acme, user):
    use_trailhead(app, acme)
    login_as(client, user)
    from app.models.discussion import Post
    with app.test_request_context(base_url=ACME):
        g.org = acme
        url = Post.query.filter_by(is_seeded=True).one().url
    anon = app.test_client()
    response = anon.post(f'{url}/reply', base_url=ACME, data={'body': 'hi'})
    assert response.status_code == 302               # sent to log in
