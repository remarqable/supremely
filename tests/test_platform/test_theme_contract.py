"""The theme contract: what a theme may ask the application for, and what
the application refuses to give it.

Every test here installs a small theme on the fly rather than leaning on a
shipped one, because that is the real claim: a theme the application has
never heard of renders an organization's content, with no controller change
and nothing registered for it.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest
from flask import g

from app.extensions import db
from app.models import Content, Upload
from app.platform.errors import ValidationError
from app.platform.theming import (
    AVAILABLE_THEMES,
    install_theme_zip,
    scan_themes,
    validate_manifest,
)
from tests.conftest import login_as, make_png, make_user

ACME = 'http://acme.example.test'

LAYOUT = """<!DOCTYPE html><html><body>
{% block content %}{% endblock %}</body></html>"""


def install(app, org, slug='probe', manifest=None, **templates):
    """Write a theme onto the data volume and activate it for `org`."""
    root = Path(app.config['DATA_DIR']) / 'themes' / slug
    root.mkdir(parents=True, exist_ok=True)
    body = {'slug': slug, 'name': slug.title(), 'version': '1.0.0'}
    body.update(manifest or {})
    (root / 'theme.json').write_text(json.dumps(body), encoding='utf-8')
    (root / 'layout.html').write_text(LAYOUT, encoding='utf-8')
    for name, content in templates.items():
        path = root / name.replace('__', '/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    scan_themes()
    org.theme = slug
    org.save()
    return root


def publish(org, title, type_slug='article', visibility='public', **kwargs):
    item = Content(type=type_slug, title=title, slug=title.lower().replace(' ', '-'),
                   body=f'Body of {title}', org_id=org.id, visibility=visibility,
                   fields={}, tags=[], **kwargs)
    item.save()
    item.publish()
    return item


# --- the data verbs -----------------------------------------------------------

def test_a_theme_renders_content_with_no_controller_change(app, client, acme, globex):
    """Acceptance 1: a front page grids the newest items of any registered
    type, and nothing in the application knows this theme exists."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'First article')
        publish(acme, 'Second article')
        publish(acme, 'A team member', type_slug='team_member')

    install(app, acme, **{'front-page.html': """{% extends site_layout %}
{% block content %}
{% for item in latest_content('article') %}<h2>{{ item.title }}</h2>{% endfor %}
<p class="count">{{ content_count('article') }}</p>
{% for person in latest_content('team_member') %}<i>{{ person.title }}</i>{% endfor %}
{% endblock %}"""})

    body = client.get('/', base_url=ACME).data
    assert b'<h2>Second article</h2>' in body
    assert b'<h2>First article</h2>' in body
    assert b'<i>A team member</i>' in body
    # Newest first, ahead of the articles provisioning seeded.
    assert body.index(b'Second article') < body.index(b'First article')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        expected = Content.feed_count('article')
    assert f'<p class="count">{expected}</p>'.encode() in body


def test_asking_for_a_type_that_does_not_exist_is_empty_not_an_error(app, client,
                                                                     acme, globex):
    """Invariant I5: empty is normal. A theme written for a vertical this
    organization does not publish still renders."""
    install(app, acme, **{'front-page.html': """{% extends site_layout %}
{% block content %}<p>{{ latest_content('recipe')|length }}/{{ content_count('recipe') }}</p>
{% endblock %}"""})
    response = client.get('/', base_url=ACME)
    assert response.status_code == 200
    assert b'<p>0/0</p>' in response.data


def test_the_limit_is_clamped(app, client, acme, globex):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for n in range(3):
            publish(acme, f'Article {n}')
        total = Content.feed_count('article')
        assert len(Content.feed('article', 2)) == 2
        # Asking past the ceiling gets the ceiling, not an error.
        assert len(Content.feed('article', 999)) == min(total, Content.FEED_LIMIT)
        assert len(Content.feed('article', 0)) == 0
        assert Content.feed('article', 'nonsense') == Content.feed('article')


def test_gated_content_is_filtered_before_the_theme_sees_it(app, client, acme,
                                                            globex):
    """Invariant I2: a theme never decides who may read something. With
    teasing off, a members-only item is not in the list at all."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Open post')
        publish(acme, 'Members post', visibility='members')
        acme.update_settings(gated_teasers=False)

    install(app, acme, **{'front-page.html': """{% extends site_layout %}
{% block content %}{% for item in latest_content('article') %}
<h2>{{ item.title }}</h2>{% endfor %}{% endblock %}"""})

    body = client.get('/', base_url=ACME).data
    assert b'Open post' in body
    assert b'Members post' not in body


def test_one_question_asked_twice_costs_one_query(app, client, acme, globex):
    """Memoized per request: two sections asking for the same list must not
    each hit the database."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Only article')

    install(app, acme, **{'front-page.html': """{% extends site_layout %}
{% block content %}
{% for item in latest_content('article', 3) %}<h2>{{ item.title }}</h2>{% endfor %}
{% for item in latest_content('article', 3) %}<h3>{{ item.title }}</h3>{% endfor %}
{% endblock %}"""})

    seen = []
    from sqlalchemy import event
    engine = db.engine

    def record(conn, cursor, statement, *args):
        if 'FROM content' in statement:
            seen.append(statement)

    event.listen(engine, 'before_cursor_execute', record)
    try:
        body = client.get('/', base_url=ACME).data
    finally:
        event.remove(engine, 'before_cursor_execute', record)

    assert b'<h2>Only article</h2>' in body and b'<h3>Only article</h3>' in body
    assert len(seen) == 1


def test_a_theme_never_sees_another_organizations_content(app, client, acme,
                                                          globex):
    """Two organizations exist so isolation is provable. Acme's front page
    must never carry a row belonging to Globex, and the reverse."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'ACME ONLY')
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        publish(globex, 'GLOBEX ONLY')

    grid = """{% extends site_layout %}
{% block content %}{% for item in latest_content('article') %}
<h2>{{ item.title }}</h2>{% endfor %}{% endblock %}"""
    install(app, acme, slug='acmetheme', **{'front-page.html': grid})
    install(app, globex, slug='globextheme', **{'front-page.html': grid})

    acme_page = client.get('/', base_url=ACME).data
    assert b'ACME ONLY' in acme_page
    assert b'GLOBEX ONLY' not in acme_page

    globex_page = client.get('/', base_url='http://globex.example.test').data
    assert b'GLOBEX ONLY' in globex_page
    assert b'ACME ONLY' not in globex_page


def test_the_memo_does_not_outlive_its_request(app, client, acme, globex):
    """`g` is application-context scoped, not request-scoped: under a held
    app context (tests, a shell, a worker) Flask reuses it across requests.
    A memo left behind would hand one organization's rows to the next
    request, which is exactly what the global tenant filter exists to make
    impossible."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'ACME ONLY')

    grid = """{% extends site_layout %}
{% block content %}{% for item in latest_content('article') %}
<h2>{{ item.title }}</h2>{% endfor %}{% endblock %}"""
    install(app, acme, slug='acmetheme', **{'front-page.html': grid})
    install(app, globex, slug='globextheme', **{'front-page.html': grid})

    with app.app_context():             # one context spanning both requests
        assert b'ACME ONLY' in client.get('/', base_url=ACME).data
        second = client.get('/', base_url='http://globex.example.test').data
    assert b'ACME ONLY' not in second


# --- template resolution ------------------------------------------------------

def test_a_type_specific_single_is_used(app, client, acme, globex):
    """Acceptance 3, and the symmetry archives already had."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'Meet Ada', type_slug='team_member')

    install(app, acme, **{
        'single.html': '{% extends site_layout %}{% block content %}GENERIC{% endblock %}',
        'single-team_member.html':
            '{% extends site_layout %}{% block content %}ROSTER PAGE{% endblock %}',
    })
    body = client.get('/team/meet-ada', base_url=ACME).data
    assert b'ROSTER PAGE' in body
    assert b'GENERIC' not in body


def test_a_theme_can_style_its_own_error_pages(app, client, acme, globex):
    """Acceptance 6: a bad URL on a branded site stays on the branded site."""
    install(app, acme, **{
        'errors__error.html':
            '{% extends site_layout %}{% block content %}OUR 404{% endblock %}',
    })
    response = client.get('/no-such-page', base_url=ACME)
    assert response.status_code == 404
    assert b'OUR 404' in response.data


def test_the_console_never_wears_the_theme(app, client, acme, globex, user):
    """The other half of the same rule: /manage is not the site."""
    install(app, acme, **{
        'errors__error.html':
            '{% extends site_layout %}{% block content %}OUR 404{% endblock %}',
    })
    login_as(client, user)
    response = client.get('/manage/nothing-here', base_url=ACME)
    assert response.status_code == 404
    assert b'OUR 404' not in response.data


# --- images and identity ------------------------------------------------------

def test_an_image_field_carries_its_alt_text_to_the_page(app, client, acme,
                                                          globex, user):
    """Acceptance 7. The theme asks for a picture; the description written
    once under Media travels with it."""
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'hero.png')},
        content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload = Upload.query.first()
        upload.alt = 'Ada at her desk'
        upload.save()
        upload_id = upload.id

    install(app, acme,
            manifest={'content': {'fields': [
                {'key': 'hero', 'type': 'image', 'label': 'Hero'}]}},
            **{'front-page.html': """{% extends site_layout %}
{% block content %}{% set img = theme_content().hero %}
{% if img %}<img src="{{ img.url('full') }}" alt="{{ img.alt }}">{% endif %}
{% endblock %}"""})

    client.post('/manage/landing', base_url=ACME, data={'hero': str(upload_id)})
    body = client.get('/', base_url=ACME).data
    assert b'alt="Ada at her desk"' in body
    assert f'/files/{upload_id}/full'.encode() in body


def test_an_image_field_refuses_a_members_only_file(app, client, acme, globex,
                                                     user):
    """A private picture behind a public page is a broken image, so the
    picker does not offer one and the form does not accept one."""
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'private.png'),
        'visibility': 'members'}, content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload_id = Upload.query.first().id

    install(app, acme, manifest={'content': {'fields': [
        {'key': 'hero', 'type': 'image', 'label': 'Hero'}]}})
    client.post('/manage/landing', base_url=ACME, data={'hero': str(upload_id)})

    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models import Organization
        saved = db.session.get(Organization, acme.id).setting('theme_content')
        assert saved['probe']['hero'] is None


def test_a_chosen_image_that_went_private_is_flagged_not_hidden(app, client,
                                                                acme, globex,
                                                                user):
    """A picture can be switched to members-only after it was chosen. The
    chooser cannot offer it (everything in it must be safe to publish), so
    the editor has to say why the field looks empty instead of quietly
    dropping it."""
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'hero.png')},
        content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload_id = Upload.query.first().id

    install(app, acme, manifest={'content': {'fields': [
        {'key': 'hero', 'type': 'image', 'label': 'Hero'}]}})
    client.post('/manage/landing', base_url=ACME, data={'hero': str(upload_id)})
    assert b'no longer available' not in client.get('/manage/landing',
                                                    base_url=ACME).data

    client.post(f'/manage/media/{upload_id}', base_url=ACME,
                data={'visibility': 'members'})
    body = client.get('/manage/landing', base_url=ACME).data
    assert b'no longer available' in body
    # And it is not offered as a choice, because choosing it would publish a
    # members-only file on a public page.
    assert f'value="{upload_id}"'.encode() not in body


def test_the_organizations_own_images_are_public_only(app, client, acme,
                                                       globex, user):
    """Same rule as a theme's image field, for the logo, favicon and hero:
    a private file behind a public page is a broken image to every visitor,
    not a private one."""
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'building.png'),
        'visibility': 'members'}, content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload_id = Upload.query.first().id

    client.post('/manage/branding', base_url=ACME, data={
        'name': 'Acme', 'description': '', 'logo_upload_id': '',
        'favicon_upload_id': '', 'hero_upload_id': str(upload_id)})
    with app.test_request_context(base_url=ACME):
        from app.models import Organization
        g.org = db.session.get(Organization, acme.id)
        assert g.org.setting('hero_upload_id') is None      # refused on write
        assert g.org.hero_image() is None

    # And one chosen while public stops being served once it goes private.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload = db.session.get(Upload, upload_id)
        upload.visibility = 'public'
        upload.save()
    client.post('/manage/branding', base_url=ACME, data={
        'name': 'Acme', 'description': '', 'logo_upload_id': '',
        'favicon_upload_id': '', 'hero_upload_id': str(upload_id)})
    client.post(f'/manage/media/{upload_id}', base_url=ACME,
                data={'visibility': 'members'})
    with app.test_request_context(base_url=ACME):
        from app.models import Organization
        g.org = db.session.get(Organization, acme.id)
        assert g.org.hero_image() is None                   # re-checked on read


def test_an_image_field_refuses_another_organizations_file(app, client, acme,
                                                            globex, user):
    login_as(client, user)
    other = make_user(email='someone@globex.test')
    other_client = app.test_client()
    login_as(other_client, other)
    with app.test_request_context(base_url=ACME):
        g.org = globex
        from app.models import Membership
        Membership.add(other.id, globex.id, role='owner')
    other_client.post('/manage/media', base_url='http://globex.example.test',
                      data={'file': (io.BytesIO(make_png()), 'theirs.png')},
                      content_type='multipart/form-data')
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        foreign_id = Upload.query.first().id

    install(app, acme, manifest={'content': {'fields': [
        {'key': 'hero', 'type': 'image', 'label': 'Hero'}]}})
    client.post('/manage/landing', base_url=ACME, data={'hero': str(foreign_id)})

    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models import Organization
        saved = db.session.get(Organization, acme.id).setting('theme_content')
        assert saved['probe']['hero'] is None


def test_the_organizations_assets_survive_a_theme_change(app, client, acme,
                                                          globex, user):
    """Acceptance 2: name, description and pictures belong to the
    organization; copy written for a layout belongs to the theme."""
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'building.png')},
        content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload_id = Upload.query.first().id
    client.post('/manage/branding', base_url=ACME, data={
        'name': 'Acme', 'description': 'We make things.',
        'logo_upload_id': '', 'favicon_upload_id': '',
        'hero_upload_id': str(upload_id)})

    for theme in ('origin', 'supremely', 'midnight'):
        acme.theme = theme
        acme.save()
        with app.test_request_context(base_url=ACME):
            g.org = db.session.get(type(acme), acme.id)
            assert g.org.hero_image().id == upload_id
            assert g.org.description == 'We make things.'


# --- manifest and package trust -----------------------------------------------

@pytest.mark.parametrize('manifest, complaint', [
    ({'name': 'No slug', 'version': '1'}, 'slug'),
    ({'slug': 'x', 'version': '1'}, 'name'),
    ({'slug': 'x', 'name': 'X'}, 'version'),
    ({'slug': 'x', 'name': 'X', 'version': '1',
      'settings': {'a': {'type': 'rainbow'}}}, 'unknown type'),
    ({'slug': 'x', 'name': 'X', 'version': '1',
      'content': {'fields': [{'type': 'text'}]}}, 'key'),
    ({'slug': 'x', 'name': 'X', 'version': '1',
      'content': {'fields': [{'key': 'a', 'type': 'carousel'}]}}, 'unknown type'),
    ({'slug': 'x', 'name': 'X', 'version': '1',
      'content': {'fields': [{'key': 'brand_name', 'type': 'text'}]}},
     'organization owns'),
])
def test_a_broken_manifest_is_refused(app, manifest, complaint):
    with pytest.raises(ValidationError) as caught:
        validate_manifest(manifest)
    assert complaint in str(caught.value)


def test_every_shipped_theme_has_a_valid_manifest(app):
    """Acceptance 4, the CI half: a built-in theme is a developer error."""
    for info in AVAILABLE_THEMES.values():
        if info['source'] != 'builtin':
            continue
        manifest = json.loads((info['path'] / 'theme.json').read_text())
        validate_manifest(manifest)


def test_an_installed_theme_with_a_broken_manifest_is_skipped(app, acme):
    """Acceptance 4, the operator half: a third-party theme never takes the
    installation down."""
    root = Path(app.config['DATA_DIR']) / 'themes' / 'broken'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'theme.json').write_text(json.dumps({'slug': 'broken'}),
                                     encoding='utf-8')
    scan_themes()                       # must not raise
    assert 'broken' not in AVAILABLE_THEMES
    assert 'origin' in AVAILABLE_THEMES


def test_a_package_carrying_an_unsupported_file_is_refused(app):
    """Acceptance 5: installing a theme writes templates, styles, scripts,
    fonts and pictures onto the data volume, and nothing else."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('theme.json', json.dumps(
            {'slug': 'sneaky', 'name': 'Sneaky', 'version': '1.0.0'}))
        zf.writestr('layout.html', LAYOUT)
        zf.writestr('payload.pyc', b'\x00\x01')
    buf.seek(0)

    with pytest.raises(ValidationError) as caught:
        install_theme_zip(buf)
    assert 'unsupported file' in str(caught.value)
    assert 'sneaky' not in AVAILABLE_THEMES
    assert not (Path(app.config['DATA_DIR']) / 'themes' / 'sneaky').exists()
