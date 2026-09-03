"""The Supremely marketing theme: the rotating headline is a neutral theme
capability — the words are per-org theme content, never baked into the
shared theme."""

from flask import g

from app.platform.theming import AVAILABLE_THEMES
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def use_supremely(app, acme, **content):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.theme = 'supremely'
        acme.save()
        if content:
            acme.update_settings(theme_content={'supremely': content})


def test_rotating_words_field_is_neutral_by_default(app):
    with app.test_request_context(base_url=ACME):
        fields = {f['key']: f for f in
                  AVAILABLE_THEMES['supremely']['content']['fields']}
        assert fields['headline_rotate']['default'] == ''


def test_the_site_name_belongs_to_the_organization(app, client, acme):
    """A public site can be named differently from the community behind it
    (site "Supremely", community "Supremely Community"). That name is the
    organization's, not the theme's: it is read from g.org.site_name, no
    theme declares it, and it survives a change of theme."""
    use_supremely(app, acme)
    acme.update_settings(site_name='Acme HQ')
    body = client.get('/', base_url=ACME).data
    assert b'Acme HQ' in body

    keys = [f['key'] for f in
            AVAILABLE_THEMES['supremely']['content']['fields']]
    assert 'brand_name' not in keys

    # Blank falls back to the community's own name.
    acme.update_settings(site_name='')
    assert acme.name.encode() in client.get('/', base_url=ACME).data


def test_the_community_keeps_its_own_name(app, client, acme, globex, user):
    """The site name renames the public site only. The community shell is
    the organization itself and keeps the organization's name."""
    use_supremely(app, acme)
    acme.update_settings(site_name='Acme HQ')
    login_as(client, user)
    shell = client.get('/discussions/', base_url=ACME).data
    assert acme.name.encode() in shell
    assert b'Acme HQ' not in shell


def test_front_page_without_rotation_uses_accent(app, client, acme):
    use_supremely(app, acme, headline_lead='Your community,',
                  headline_accent='all in one place.')
    page = client.get('/', base_url=ACME)
    assert page.status_code == 200
    assert b'all in one place.' in page.data
    assert b'sup-rotate' not in page.data


def test_front_page_rotation_is_its_own_line(app, client, acme):
    use_supremely(app, acme,
                  headline_lead='The open-source community platform.',
                  headline_accent='all in one place.',
                  rotate_lead='Be Supremely',
                  headline_rotate='Bold., Fast., You.')
    page = client.get('/', base_url=ACME)
    assert page.status_code == 200
    # Headline and accent line render as authored, with the rotating line
    # beneath them — static lead plus accent word. Without JS the last word
    # renders; the full list rides along as data for the script, which must
    # be an external asset (the CSP blocks inline scripts).
    assert b'The open-source community platform.' in page.data
    assert b'all in one place.' in page.data
    assert b'Be Supremely' in page.data
    assert b'data-words="Bold., Fast., You."' in page.data
    assert b'>You.</span>' in page.data
    assert b'rotate.js' in page.data
    assert b'<script>' not in page.data


def test_rotate_script_is_a_theme_asset(app, client, acme):
    use_supremely(app, acme)
    asset = client.get('/themes/supremely/static/rotate.js', base_url=ACME)
    assert asset.status_code == 200
    assert b'sup-rotate-out' in asset.data
