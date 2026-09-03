"""Device-aware rendering.

The whole value of the pattern is what happens when a mobile template is
*absent*, which is almost always. These tests write real templates into the
view tree and remove them again, because resolution is the behaviour under
test and a mock of it would prove nothing.
"""

import pytest
from flask import g
from jinja2 import ChoiceLoader, FileSystemLoader

from app.platform.devices import (
    detect_device,
    device_candidates,
    device_type,
    is_mobile,
    mobile_variant,
)
from tests.conftest import login_as

ACME = 'http://acme.example.test'
IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
          'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148')
DESKTOP = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
           'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')


@pytest.fixture
def mobile_template(app, tmp_path):
    """Add a template to the search path for one test.

    Written under tmp_path and reachable through an extra loader, not into
    app/views: a killed test run would otherwise leave stray templates in
    the versioned tree, where `make css` scans them and their classes leak
    into the committed stylesheet. It also lets two test runs share a
    checkout without racing on the same paths.

    The loader goes last, so a real template always wins and only names the
    application does not provide can be introduced here, which is exactly
    the mobile case under test.
    """
    root = tmp_path / 'views'
    root.mkdir()
    app.jinja_env.loader = ChoiceLoader([app.jinja_env.loader,
                                         FileSystemLoader(str(root))])

    def write(name: str, body: str):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
        # Jinja caches by name, and a miss is cached like a hit.
        app.jinja_env.cache.clear()
        return path

    yield write
    app.jinja_env.cache.clear()


# --- naming and detection -----------------------------------------------------

def test_the_mobile_sibling_of_a_template():
    assert mobile_variant('manage/media.html') == 'manage/mobile/media.html'
    assert mobile_variant('community/single.html') == 'community/mobile/single.html'
    assert mobile_variant('themes/origin/single.html') == (
        'themes/origin/mobile/single.html')
    assert mobile_variant('media.html') == 'mobile/media.html'


def test_a_phone_is_recognised(app):
    with app.test_request_context(base_url=ACME, headers={'User-Agent': IPHONE}):
        assert detect_device() == 'mobile'
        assert is_mobile()
    with app.test_request_context(base_url=ACME, headers={'User-Agent': DESKTOP}):
        assert detect_device() == 'desktop'
        assert not is_mobile()


def test_no_request_is_a_desktop(app):
    """Jobs and CLI commands render too, and must not touch the session."""
    with app.app_context():
        assert device_type() == 'desktop'
        assert not is_mobile()


def test_the_query_parameter_wins_and_is_remembered(app, client, acme, globex):
    """A developer holds a mobile layout open on a laptop; a reader who
    prefers one keeps it."""
    with app.test_request_context('/?device=mobile', base_url=ACME,
                                  headers={'User-Agent': DESKTOP}):
        assert device_type() == 'mobile'
    with app.test_request_context('/?device=nonsense', base_url=ACME,
                                  headers={'User-Agent': DESKTOP}):
        assert device_type() == 'desktop'      # unknown value is ignored

    # Stickiness itself is proved through the client, in
    # test_a_reader_can_get_back_to_automatic_detection.


def test_the_candidate_list_pairs_each_name_with_its_sibling(app):
    names = ['community/single.html', 'themes/origin/single.html']
    with app.test_request_context(base_url=ACME, headers={'User-Agent': DESKTOP}):
        assert device_candidates(names) == names       # untouched on desktop
    with app.test_request_context(base_url=ACME, headers={'User-Agent': IPHONE}):
        assert device_candidates(names) == [
            'community/mobile/single.html', 'community/single.html',
            'themes/origin/mobile/single.html', 'themes/origin/single.html',
        ]


# --- rendering ----------------------------------------------------------------

def test_a_phone_gets_the_ordinary_page_when_no_mobile_version_exists(
        app, client, acme, globex):
    """The case that holds for almost every page: nothing to add, nothing
    to maintain, and a phone is served exactly what a laptop is."""
    phone = client.get('/', base_url=ACME, headers={'User-Agent': IPHONE})
    laptop = client.get('/', base_url=ACME, headers={'User-Agent': DESKTOP})
    assert phone.status_code == 200
    assert phone.data == laptop.data           # byte for byte the same page


def test_a_mobile_template_is_used_when_one_exists(app, client, acme, globex,
                                                   mobile_template):
    mobile_template('themes/origin/mobile/front-page.html',
                    '{% extends site_layout %}'
                    '{% block content %}PHONE FRONT PAGE{% endblock %}')

    phone = client.get('/', base_url=ACME, headers={'User-Agent': IPHONE})
    assert b'PHONE FRONT PAGE' in phone.data

    laptop = client.get('/', base_url=ACME, headers={'User-Agent': DESKTOP})
    assert b'PHONE FRONT PAGE' not in laptop.data


def test_a_mobile_template_does_not_outrank_a_more_specific_desktop_one(
        app, client, acme, globex, mobile_template):
    """A theme's mobile/single.html must not beat the community shell's
    single.html: it replaces the template it is the mobile version of, not
    whichever one happens to be further down the chain."""
    mobile_template('themes/origin/mobile/single.html',
                    '{% extends site_layout %}'
                    '{% block content %}THEME PHONE SINGLE{% endblock %}')
    with app.test_request_context(base_url=ACME):
        from app.models import Content
        g.org = acme
        item = Content(type='article', title='An article', slug='an-article',
                       body='Body', org_id=acme.id, visibility='public',
                       fields={}, tags=[])
        item.save()
        item.publish()

    # The shell serves this surface, so the shell's template still wins.
    body = client.get('/blog/an-article', base_url=ACME,
                      headers={'User-Agent': IPHONE}).data
    assert b'THEME PHONE SINGLE' not in body
    assert b'An article' in body


def test_the_console_can_have_a_mobile_page(app, client, acme, globex, user,
                                            mobile_template):
    """Every console screen goes through the device-aware renderer, so
    giving one a mobile version is adding a file, not editing Python."""
    login_as(client, user)
    mobile_template('manage/mobile/media.html',
                    '{% extends "manage/_layout.html" %}'
                    '{% block manage_content %}PHONE MEDIA{% endblock %}')

    phone = client.get('/manage/media', base_url=ACME,
                       headers={'User-Agent': IPHONE})
    assert b'PHONE MEDIA' in phone.data
    laptop = client.get('/manage/media', base_url=ACME,
                        headers={'User-Agent': DESKTOP})
    assert b'PHONE MEDIA' not in laptop.data


def test_a_theme_part_can_have_a_mobile_version(app, client, acme, globex,
                                                mobile_template):
    """themed() resolves parts the same way, so a theme can swap only its
    header on a phone."""
    mobile_template('themes/origin/mobile/header.html',
                    '<header>PHONE HEADER</header>')
    phone = client.get('/', base_url=ACME, headers={'User-Agent': IPHONE})
    assert b'PHONE HEADER' in phone.data
    laptop = client.get('/', base_url=ACME, headers={'User-Agent': DESKTOP})
    assert b'PHONE HEADER' not in laptop.data


def test_the_active_themes_own_part_beats_origins_mobile_one(
        app, client, acme, globex, mobile_template):
    """The inversion this ordering exists to prevent.

    With a non-Origin theme active, Origin's mobile header must not replace
    that theme's own header on a phone: a mobile variant may only displace
    the template it is the mobile version of, and Origin sits below the
    active theme in the chain.
    """
    acme.theme = 'supremely'          # ships its own header.html
    acme.save()
    mobile_template('themes/origin/mobile/header.html',
                    '<header>ORIGIN PHONE HEADER</header>')

    body = client.get('/', base_url=ACME, headers={'User-Agent': IPHONE}).data
    assert b'ORIGIN PHONE HEADER' not in body

    # And the active theme's own mobile header does win, so the test above
    # is proving order rather than that mobile parts never resolve.
    mobile_template('themes/supremely/mobile/header.html',
                    '<header>SUPREMELY PHONE HEADER</header>')
    body = client.get('/', base_url=ACME, headers={'User-Agent': IPHONE}).data
    assert b'SUPREMELY PHONE HEADER' in body


def test_the_shell_gets_a_mobile_layout_when_one_exists(
        app, client, acme, globex, user, mobile_template):
    """The community shell is app-owned, so its mobile layout is app-owned
    too, and it reaches the pages that extend the layout by name as well as
    those that receive it from render_site."""
    login_as(client, user)
    mobile_template('layouts/mobile/community.html',
                    '<!DOCTYPE html><html><body>PHONE SHELL'
                    '{% block content %}{% endblock %}</body></html>')

    through_the_seam = client.get('/discussions/', base_url=ACME,
                                  headers={'User-Agent': IPHONE})
    assert b'PHONE SHELL' in through_the_seam.data

    by_name = client.get('/dashboard', base_url=ACME,
                         headers={'User-Agent': IPHONE})
    assert b'PHONE SHELL' in by_name.data

    laptop = client.get('/discussions/', base_url=ACME,
                        headers={'User-Agent': DESKTOP})
    assert b'PHONE SHELL' not in laptop.data


def test_a_reader_can_get_back_to_automatic_detection(app, client, acme,
                                                      globex, mobile_template):
    """?device= pins a choice for the session, so there has to be a way
    out of it, or one stray link leaves someone stuck."""
    mobile_template('themes/origin/mobile/front-page.html',
                    '{% extends site_layout %}'
                    '{% block content %}PHONE FRONT PAGE{% endblock %}')
    laptop = {'User-Agent': DESKTOP}

    pinned = client.get('/?device=mobile', base_url=ACME, headers=laptop)
    assert b'PHONE FRONT PAGE' in pinned.data
    # Sticky: no parameter this time, still the mobile layout.
    assert b'PHONE FRONT PAGE' in client.get('/', base_url=ACME,
                                             headers=laptop).data

    client.get('/?device=auto', base_url=ACME, headers=laptop)
    assert b'PHONE FRONT PAGE' not in client.get('/', base_url=ACME,
                                                 headers=laptop).data


def test_html_responses_vary_on_user_agent(client, acme, globex):
    """A cache in front of this serves one URL two ways, so it has to know."""
    page = client.get('/', base_url=ACME)
    assert 'User-Agent' in page.headers.get('Vary', '')

    asset = client.get('/static/css/app.css', base_url=ACME)
    assert 'User-Agent' not in (asset.headers.get('Vary') or '')
