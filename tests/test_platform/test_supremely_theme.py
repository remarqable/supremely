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


def test_front_page_without_rotation_uses_accent(app, client, acme):
    use_supremely(app, acme, headline_lead='Your community,',
                  headline_accent='all in one place.')
    page = client.get('/', base_url=ACME)
    assert page.status_code == 200
    assert b'all in one place.' in page.data
    assert b'sup-rotate' not in page.data


def test_front_page_rotation_settles_on_last_word(app, client, acme):
    use_supremely(app, acme, headline_lead='Be Supremely',
                  headline_rotate='Bold., Curious., You.')
    page = client.get('/', base_url=ACME)
    assert page.status_code == 200
    # The no-JS/reduced-motion fallback renders the settled last word; the
    # full list rides along as data for the script.
    assert b'data-words="Bold., Curious., You."' in page.data
    assert b'>You.</span>' in page.data
    assert b'sup-rotate' in page.data
