"""The Supremely marketing theme: the rotating headline is a neutral theme
capability — the words are per-org theme content, never baked into the
shared theme."""

from flask import g

from app.platform.theming import AVAILABLE_THEMES

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


def test_brand_name_is_separate_from_org_name(app, client, acme):
    """The marketing header/footer can carry their own site name — the
    community keeps the org name (e.g. site "Supremely", community
    "Supremely Community"). Blank falls back to the org name."""
    use_supremely(app, acme, brand_name='Acme HQ')
    body = client.get('/', base_url=ACME).data
    assert b'Acme HQ' in body


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
