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
from tests.conftest import enable_types, login_as, make_png, make_user

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
    enable_types(acme, 'team_member')
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


def test_a_theme_asks_which_sections_the_site_shows(app, client, acme):
    """The window is a theme verb, not a section the application draws.

    site_entries() answers which types this organization advertises and in
    what order; the theme decides entirely what one looks like. A theme that
    never calls it has no window, which is what Origin had before this.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'An article')
    acme.set_type_settings('article', site_entry=True)
    install(app, acme, **{'front-page.html': """{% extends site_layout %}
{% block content %}
{% for ct in site_entries() %}<h2 class="section">{{ ct.plural }}</h2>
{% for item in latest_content(ct.slug, 3) %}<a href="{{ item.permalink }}">{{ item.title }}</a>{% endfor %}
{% endfor %}
{% endblock %}"""})

    body = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'class="section"' in body
    assert 'An article' in body
    assert '/blog/an-article' in body      # the community address, not a copy


def test_a_theme_can_restyle_one_section_without_touching_the_rest(app, client,
                                                                   acme):
    """The override the partial exists to allow: site-feed-{type}.html for
    one type, and everything else keeps the default."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'An article')
        publish(acme, 'An episode', type_slug='episode')
    acme.set_type_settings('article', site_entry=True)
    acme.set_type_settings('episode', site_entry=True)

    install(app, acme, **{
        'site-feed-episode.html':
            '<div class="my-podcast">{{ content_type.plural }}</div>',
    })
    body = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'class="my-podcast"' in body        # the theme's own section
    assert 'Latest Blog' in body               # the default, still there


def test_the_order_is_the_organizations(app, client, acme, globex):
    """Two organizations can advertise the same types in different orders,
    and a site with no opinion still shows them the same way every time."""
    from app.platform.content_types import site_entry_types
    acme.set_type_settings('episode', site_entry=True, site_entry_position=0)
    acme.set_type_settings('article', site_entry=True, site_entry_position=1)
    globex.set_type_settings('article', site_entry=True, site_entry_position=0)
    globex.set_type_settings('episode', site_entry=True, site_entry_position=1)

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert [ct.slug for ct in site_entry_types()] == ['episode', 'article']
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        assert [ct.slug for ct in site_entry_types()] == ['article', 'episode']
    # Neither organization's choice reached the other, and one that has made
    # no choice at all still advertises nothing.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('episode', site_entry=None,
                               site_entry_position=None)
        acme.set_type_settings('article', site_entry=None,
                               site_entry_position=None)
        assert site_entry_types() == []
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        assert [ct.slug for ct in site_entry_types()] == ['article', 'episode']


def test_every_bundled_theme_can_show_the_window(app, client, acme):
    """A theme that ships its own front page has to offer the window too,
    or that site simply has none. Origin was updated and the two themes with
    their own front-page.html were not, so half the bundled themes had no
    shop window at all."""
    from app.platform.theming import AVAILABLE_THEMES
    with app.test_request_context(base_url=ACME):
        g.org = acme
        publish(acme, 'A shopfront article')
    acme.set_type_settings('article', site_entry=True)

    for slug in AVAILABLE_THEMES:
        acme.theme = slug
        acme.save()
        body = client.get('/', base_url=ACME).get_data(as_text=True)
        assert 'A shopfront article' in body, slug


def test_every_bundled_theme_switches_navigation_off_in_a_preview(
        app, client, acme, user):
    """A preview must not let the reader click away from the draft, and a
    theme that draws its own chrome decides that for itself.

    Both halves are checked together because they belong together: the lock
    without the banner is a page with nothing on it that goes anywhere.

    An article and a page, because those resolve differently. No bundled
    theme ships single.html, so an article-only loop would look like four
    cases and exercise one file. Pages are where the themes diverge, and
    where a missing banner last hid.
    """
    from app.platform.theming import AVAILABLE_THEMES
    from tests.conftest import login_as
    with app.test_request_context(base_url=ACME):
        g.org = acme
        article = publish(acme, 'A draft to look at')
        page = publish(acme, 'A draft page', type_slug='page')
        ids = {'article': article.id, 'page': page.id}
    client = login_as(client, user)

    for slug in AVAILABLE_THEMES:
        acme.theme = slug
        acme.save()
        for kind, content_id in ids.items():
            label = f'{slug}/{kind}'
            body = client.get(f'/manage/content/{content_id}/preview',
                              base_url=ACME).get_data(as_text=True)
            # Each end separately: one assertion over both would pass with
            # the header wide open so long as the footer was shut.
            assert 'inert' in body.split('<main')[0], f'{label} header'
            assert 'inert' in body.rsplit('</main>', 1)[-1], f'{label} footer'
            assert 'Back to the editor' in body, label


def test_a_theme_with_its_own_layout_still_says_it_is_a_preview(
        app, client, acme, user):
    """A theme the application has never heard of, shipping a layout of its
    own and nothing else. That is the ordinary shape of a theme, and the
    layout is the one file nearly all of them replace.

    Such a theme gets no lock, which is a theme author's mistake to make and
    the docs say so. What it must not lose is the banner, or a draft under
    it looks exactly like the live site. It keeps it because Origin's
    content templates carry one too, and the banner shows once per request.
    """
    from tests.conftest import login_as
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = publish(acme, 'A draft to look at')
        content_id = item.id
    install(app, acme)
    client = login_as(client, user)
    body = client.get(f'/manage/content/{content_id}/preview',
                      base_url=ACME).get_data(as_text=True)
    assert 'A draft to look at' in body
    assert 'not published yet' in body
    assert 'Back to the editor' in body


def test_the_banner_is_never_shown_twice(app, client, acme, user):
    """Two places include it so that overriding either one cannot lose it,
    which is only safe because it renders once."""
    from app.platform.theming import AVAILABLE_THEMES
    from tests.conftest import login_as
    with app.test_request_context(base_url=ACME):
        g.org = acme
        ids = [publish(acme, 'A draft to look at').id,
               publish(acme, 'A draft page', type_slug='page').id]
    client = login_as(client, user)
    for slug in AVAILABLE_THEMES:
        acme.theme = slug
        acme.save()
        for content_id in ids:
            body = client.get(f'/manage/content/{content_id}/preview',
                              base_url=ACME).get_data(as_text=True)
            assert body.count('not published yet') == 1, slug


def test_a_theme_part_is_not_a_page_template(app, acme):
    """`template` is free text on the page form and reaches the theme
    chain. A layout, a header and a footer are pieces a page is assembled
    from, not pages: rendered on their own they are fragments with no
    document around them, and nothing a page should carry reaches them."""
    from app.platform.theming import page_template_allowed
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for part in ('layout', 'header', 'footer'):
            assert not page_template_allowed(part), part
        # Still a page template, and still not one the application owns.
        assert page_template_allowed('front-page')
        assert not page_template_allowed('archive')


def test_a_locked_preview_always_carries_the_way_out(app, client, acme, user):
    """A page names its own template, and the value reaches the theme chain,
    so a preview can land on a template nobody wrote for previews. Whatever
    it lands on, it must not be a page with the navigation off and no way to
    leave.

    Under every theme, because which file a name resolves to depends on the
    theme, and the assertion is unconditional: a version of this that only
    checked the exit "if the page was locked" would have gone quiet the
    moment the lock went missing.
    """
    from app.platform.theming import AVAILABLE_THEMES
    from tests.conftest import login_as
    login_as(client, user)
    ids = {}
    for template in ('front-page', 'gate', 'subscribe', 'layout',
                     'archive-team_member'):
        with app.test_request_context(base_url=ACME):
            g.org = acme
            # The helper writes the slug from the title, and a title
            # carrying an underscore would not pass validation.
            item = publish(acme, 'Draft ' + template.replace('_', ' '),
                           type_slug='page', template=template)
            ids[template] = item.id
    for slug in AVAILABLE_THEMES:
        acme.theme = slug
        acme.save()
        for template, content_id in ids.items():
            body = client.get(f'/manage/content/{content_id}/preview',
                              base_url=ACME).get_data(as_text=True)
            assert 'Back to the editor' in body, f'{slug}/{template}'


def test_a_theme_can_replace_the_block_section_and_one_block(app, client, acme):
    """Both block seams go through the theme chain.

    The wrapper used to be a literal include path, so a theme shipping its
    own _content_blocks.html was ignored unless it also overrode
    single.html -- which is not what the theme contract promises.
    """
    acme.set_type_settings('recipe', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        host = publish(acme, 'Host article')
        card = Content(type='recipe', title='A card', body='Mix it.',
                       org_id=acme.id, fields={}, tags=[],
                       visibility='public', parent_id=host.id)
        card.save()
        card.publish()

    install(app, acme, **{
        '_content_blocks.html':
            '<div class="my-blocks">'
            '{% for block in content.visible_children() %}'
            '{% include block_template(block) with context %}'
            '{% endfor %}</div>',
        'content-block-recipe.html':
            '<p class="my-recipe">{{ block.title }}</p>',
    })
    body = client.get('/blog/host-article', base_url=ACME).get_data(as_text=True)
    assert 'class="my-blocks"' in body        # the theme's own wrapper
    assert 'class="my-recipe"' in body        # and its own block
    assert 'A card' in body


def test_every_bundled_theme_renders_the_blocks_in_a_page(app, client, acme):
    """A theme that ships its own page template has to draw blocks too.

    The blocks section was added to the community templates and to Origin
    and not to the Supremely theme's own page.html, so a page's blocks were
    silently dropped for anyone using it. Same shape of miss as the front
    page window, so it gets the same shape of test: vary the theme.
    """
    from app.platform.theming import AVAILABLE_THEMES
    acme.set_type_settings('recipe', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='About us', slug='about-us',
                       body='Who we are.', org_id=acme.id, fields={}, tags=[],
                       visibility='public')
        page.save()
        page.publish()
        card = Content(type='recipe', title='Inline card', body='Mix it.',
                       org_id=acme.id, fields={}, tags=[],
                       visibility='public', parent_id=page.id)
        card.save()
        card.publish()

    for slug in AVAILABLE_THEMES:
        acme.theme = slug
        acme.save()
        body = client.get('/about-us', base_url=ACME).get_data(as_text=True)
        assert 'Who we are.' in body, slug
        assert 'Inline card' in body, slug


def test_the_documented_theme_recipe_is_the_one_that_works(app, client, acme):
    """A theme following docs/themes/README.md verbatim gets what the doc
    promises. The recipe used to be a literal include path, which quietly
    ignored the theme's own wrapper."""
    acme.set_type_settings('recipe', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        host = publish(acme, 'Host article')
        card = Content(type='recipe', title='Inline card', body='Mix it.',
                       org_id=acme.id, fields={}, tags=[],
                       visibility='public', parent_id=host.id)
        card.save()
        card.publish()

    install(app, acme, **{
        # Exactly the two snippets the documentation gives.
        'single.html': """{% extends site_layout %}
{% block content %}<div>{{ content.html | safe }}</div>
{% include blocks_template() with context %}{% endblock %}""",
        '_content_blocks.html':
            '<div class="doc-wrapper">'
            '{% for block in content.visible_children() %}'
            '{% include block_template(block) with context %}'
            '{% endfor %}</div>',
    })
    body = client.get('/blog/host-article', base_url=ACME).get_data(as_text=True)
    assert 'class="doc-wrapper"' in body
    assert 'Inline card' in body


def test_no_theme_sorts_an_archive_for_itself(app):
    """The archive query answers in the type's declared order, so a theme
    has nothing left to correct. A template that sorts is a theme working
    around the model, and the next type with the same problem would need
    the same workaround written again.

    Themes only. The community shell and the console are application
    templates and may sort a list in Jinja for their own reasons; a theme
    may not, because a theme is a renderer.
    """
    import re
    from pathlib import Path
    themes = Path(__file__).parents[2] / 'app' / 'views' / 'themes'
    # Any sort filter, not only the one spelling that was there: `| sort`
    # and `| sort(reverse=True)` are the same workaround.
    sorting = re.compile(r'\|\s*sort\b')
    offenders = [str(path.relative_to(themes))
                 for path in themes.rglob('*.html')
                 if sorting.search(path.read_text(encoding='utf-8'))]
    assert offenders == []


def test_every_partial_seam_finds_a_mobile_variant(app, client, acme):
    """All four partial seams resolve the same way, the application's own
    fallback included.

    Each seam used to spell its fallback as a bare `partials/x.html`, which
    skips device resolution, so shipping `partials/mobile/_embed.html` would
    never have been found on a phone. There are four of these and they must
    not drift apart again.
    """
    from pathlib import Path

    from app.platform.theming import (
        block_template,
        blocks_template,
        embed_template,
        site_feed_template,
    )
    views = Path(__file__).parents[2] / 'app' / 'views' / 'partials'
    mobile = views / 'mobile'
    mobile.mkdir(exist_ok=True)
    written = []
    try:
        for name in ('_site_feed.html', '_embed.html', '_content_block.html',
                     '_content_blocks.html'):
            path = mobile / name
            path.write_text('<p>phone</p>', encoding='utf-8')
            written.append(path)

        with app.test_request_context('/?device=mobile', base_url=ACME):
            g.org = acme
            from app.platform.content_types import get_content_type
            article = get_content_type('article')
            item = type('T', (), {'type': 'article'})()
            resolved = [site_feed_template(article), embed_template(item),
                        block_template(item), blocks_template()]
        for name in resolved:
            assert '/mobile/' in name, name
    finally:
        for path in written:
            path.unlink()
        if not any(mobile.iterdir()):
            mobile.rmdir()
