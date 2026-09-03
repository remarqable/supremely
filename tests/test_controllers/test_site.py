import pytest
from flask import g

from app.extensions import db
from app.models import Content, NavigationItem
from app.platform.theming import page_template_exists
from tests.conftest import login_as, make_user


def publish_page(app, org, slug='story', title='Our Story', body='Our story.',
                 visibility='public', **kwargs):
    with app.test_request_context():
        g.org = org
        page = Content(type='page', title=title, slug=slug, body=body,
                       org_id=org.id, visibility=visibility, fields={}, tags=[],
                       **kwargs)
        page.save()
        page.publish()
        page_id = page.id
    db.session.expire_all()
    return page_id


ACME = 'http://acme.example.test'


def test_public_page_renders(app, client, acme, globex):
    publish_page(app, acme)
    response = client.get('/story', base_url=ACME)
    assert response.status_code == 200
    assert b'Our Story' in response.data
    assert b'Our story.' in response.data


def test_draft_page_404(app, client, acme, globex):
    with app.test_request_context():
        g.org = acme
        Content(type='page', title='Secret', slug='secret', org_id=acme.id, fields={}, tags=[]).save()
    assert client.get('/secret', base_url=ACME).status_code == 404


def test_unknown_page_404(client, acme, globex):
    assert client.get('/nope', base_url=ACME).status_code == 404


def test_pages_are_tenant_isolated(app, client, acme, globex):
    publish_page(app, acme, body='Acme story')
    publish_page(app, globex, body='Globex story')
    acme_page = client.get('/story', base_url=ACME)
    globex_page = client.get('/story', base_url='http://globex.example.test')
    assert b'Acme story' in acme_page.data
    assert b'Globex story' not in acme_page.data
    assert b'Globex story' in globex_page.data


def test_member_only_page_gates_anonymous(app, client, acme, globex):
    # Tease-don't-hide: a friendly 200 gate with the title and a login CTA,
    # never the body.
    publish_page(app, acme, slug='inside', visibility='members',
                 body='Secret handshake')
    response = client.get('/inside', base_url=ACME)
    assert response.status_code == 200
    assert b'Secret handshake' not in response.data
    assert b'Members only' in response.data
    assert b'/auth/login' in response.data


def test_member_only_page_visible_to_member(app, client, acme, globex, user):
    publish_page(app, acme, slug='inside', visibility='members')
    login_as(client, user)
    assert client.get('/inside', base_url=ACME).status_code == 200


def test_member_only_page_gated_for_non_member(app, client, acme, globex):
    publish_page(app, acme, slug='inside', visibility='members',
                 body='Secret handshake')
    outsider = make_user(email='out@example.com')
    login_as(client, outsider)
    # Logged in but not a member: same gate, no login CTA, never the body.
    response = client.get('/inside', base_url=ACME)
    assert response.status_code == 200
    assert b'Secret handshake' not in response.data
    assert b'Members only' in response.data


def publish_article(app, org, slug, title, body, visibility='public'):
    with app.test_request_context():
        g.org = org
        article = Content(type='article', title=title, slug=slug, body=body,
                          org_id=org.id,
                          visibility=visibility, fields={}, tags=[])
        article.save()
        article.publish()
        return article.permalink


def test_archive_teases_gated_items(app, client, acme, globex):
    """Members-only items appear in public archives as locked titles —
    excerpt and body withheld — and their permalink lands on the gate."""
    publish_article(app, acme, 'open', 'Open Article', 'Everyone reads this.')
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    listing = client.get('/blog', base_url=ACME)
    assert b'Open Article' in listing.data
    assert b'Everyone reads this.' in listing.data
    assert b'Closed Article' in listing.data          # title teased
    assert b'The inner circle' not in listing.data    # excerpt withheld
    assert b'Members only' in listing.data

    gate = client.get(permalink, base_url=ACME)
    assert gate.status_code == 200
    assert b'Closed Article' in gate.data
    assert b'The inner circle' not in gate.data
    assert b'/auth/login' in gate.data


def set_teasers(app, org, enabled):
    with app.test_request_context():
        g.org = org
        org.update_settings(gated_teasers=enabled)


def test_teasers_off_hides_gated_content(app, client, acme, globex):
    """With the org's tease switch off, gated items vanish from public
    lists and direct hits degrade to login redirect (anonymous) / 404
    (signed-in non-member) — the title never renders."""
    publish_article(app, acme, 'open', 'Open Article', 'Everyone reads this.')
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    set_teasers(app, acme, False)

    listing = client.get('/blog', base_url=ACME)
    assert b'Open Article' in listing.data
    assert b'Closed Article' not in listing.data

    response = client.get(permalink, base_url=ACME)
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']

    outsider = make_user(email='outsider@example.com')
    login_as(client, outsider)
    assert client.get(permalink, base_url=ACME).status_code == 404


def test_teasers_off_member_still_reads(app, client, acme, globex, user):
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    set_teasers(app, acme, False)
    login_as(client, user)
    page = client.get(permalink, base_url=ACME)
    assert page.status_code == 200
    assert b'The inner circle only.' in page.data


def test_manage_privacy_toggle(app, client, acme, globex, user):
    login_as(client, user)                       # acme's owner
    response = client.post('/manage/settings/privacy', base_url=ACME,
                           data={})                       # checkbox absent
    assert response.status_code == 302
    assert acme.teases_gated_content() is False
    client.post('/manage/settings/privacy', base_url=ACME,
                data={'gated_teasers': 'on'})
    assert acme.teases_gated_content() is True


def test_section_lock_gates_the_whole_section(app, client, acme, globex, user):
    """Manage → Content types lock: every item in the section gates for
    non-members, item visibility notwithstanding; members unaffected."""
    permalink = publish_article(app, acme, 'open', 'Open Article',
                                'Everyone reads this.')       # public item
    owner = app.test_client()
    login_as(owner, user)
    response = owner.post('/manage/content-types/article/visibility',
                          base_url=ACME)
    assert response.status_code == 302
    assert acme.setting('section_visibility') == {'article': 'members'}

    listing = client.get('/blog', base_url=ACME)
    assert listing.status_code == 200                 # one gate for the area
    assert b'Open Article' not in listing.data
    assert b'Members only' in listing.data
    single = client.get(permalink, base_url=ACME)
    assert b'Everyone reads this.' not in single.data
    assert b'Members only' in single.data

    member_view = owner.get('/blog', base_url=ACME)
    assert b'Open Article' in member_view.data

    # Toggle back: public again.
    owner.post('/manage/content-types/article/visibility', base_url=ACME)
    assert acme.setting('section_visibility') == {}
    assert b'Everyone reads this.' in client.get(permalink,
                                                 base_url=ACME).data


def test_section_lock_rejects_standalone_types(app, client, acme, globex, user):
    login_as(client, user)
    assert client.post('/manage/content-types/page/visibility',
                       base_url=ACME).status_code == 404


def test_gated_single_readable_by_member(app, client, acme, globex, user):
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    login_as(client, user)
    response = client.get(permalink, base_url=ACME)
    assert response.status_code == 200
    assert b'The inner circle only.' in response.data


def test_home_page_is_theme_hero(app, client, acme, globex):
    """The home page is the active theme's front page, edited as theme content
    (Manage → Home page) — not a CMS page. Origin renders an editable hero."""
    acme.update_settings(theme_content={'origin': {
        'headline': 'This is the home page.'}})
    response = client.get('/', base_url=ACME)
    assert b'This is the home page.' in response.data


def test_navigation_rendered(app, client, acme, globex):
    page_id = publish_page(app, acme)
    with app.test_request_context():
        g.org = acme
        NavigationItem(menu='primary', label='Story', content_id=page_id,
                       org_id=acme.id, position=99).save()
        NavigationItem(menu='footer', label='Imprint', url='https://x.test',
                       org_id=acme.id, position=99).save()
    response = client.get('/', base_url=ACME)
    assert b'href="/story"' in response.data
    assert b'https://x.test' in response.data


def test_theme_override_applies(app, client, acme, globex):
    acme.theme = 'midnight'
    acme.save()
    response = client.get('/', base_url=ACME)
    assert response.status_code == 200
    assert b'midnight' in response.data       # theme layout class
    assert b'theme.css' in response.data

    # Other org unaffected: presentation is per-tenant
    other = client.get('/', base_url='http://globex.example.test')
    assert b'midnight' not in other.data


def test_theme_asset_served(client, acme, globex):
    acme.theme = 'midnight'
    acme.save()
    response = client.get('/themes/midnight/static/theme.css', base_url=ACME)
    assert response.status_code == 200
    assert b'--midnight-accent' in response.data


def test_unknown_theme_asset_404(client, acme, globex):
    assert client.get('/themes/evil/static/x.css',
                      base_url=ACME).status_code == 404


# --- a page's template must not reach an application template ------------------------
ACME_HOST = 'http://acme.example.test'


@pytest.mark.parametrize('template', [
    'discussion-post',   # renders community/discussion-post.html, 500s on a page
    'discussion-group',
    'archive',
    'single',
    'members',
    'discussions',
    'newsletters',
])
def test_community_templates_are_not_offerable_as_a_page_template(app, acme, template):
    """render_site puts community/ ahead of every theme candidate, so picking
    one of these names renders the application's own page without the context
    it needs. Origin ships several of the same names, so merely existing in a
    theme is not enough to make a name safe."""
    with app.test_request_context(base_url=ACME_HOST):
        g.org = acme
        assert page_template_exists(template) is False


def test_every_offerable_template_renders_for_a_visitor(app, client, acme):
    """The half the model regex cannot see: whatever survives validation has
    to actually render on a public URL, for an anonymous visitor."""
    offerable = []
    with app.test_request_context(base_url=ACME_HOST):
        g.org = acme
        for name in ('page', 'front-page', 'gate', 'header', 'footer', 'layout',
                     'subscribe', 'confirm', 'unsubscribe'):
            if page_template_exists(name):
                offerable.append(name)
    assert offerable, 'expected the theme to offer at least one page template'

    page_id = publish_page(app, acme, slug='tmpl-render')
    for name in offerable:
        page = db.session.get(Content, page_id)
        page.template = name
        db.session.add(page)
        db.session.commit()
        response = client.get('/tmpl-render', base_url=ACME_HOST)
        assert response.status_code == 200, f'{name} returned {response.status_code}'


def test_a_template_the_theme_no_longer_provides_falls_back(app, client, acme):
    page_id = publish_page(app, acme, slug='tmpl-stale')
    page = db.session.get(Content, page_id)
    page.template = 'wide-legacy'
    db.session.add(page)
    db.session.commit()

    response = client.get('/tmpl-stale', base_url=ACME_HOST)
    assert response.status_code == 200
    assert b'Our story.' in response.data


def test_editor_refuses_a_template_the_theme_does_not_provide(app, client, acme, user):
    page_id = publish_page(app, acme, slug='tmpl-form')
    login_as(client, user)

    response = client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                           data={'title': 'T', 'slug': 'tmpl-form', 'body': 'b',
                                 'status': 'published', 'visibility': 'public',
                                 'template': 'discussion-post'},
                           follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).template is None
    assert b'No template called' in response.data


def test_editor_still_saves_a_page_carrying_a_stranded_template(app, client, acme,
                                                                user):
    """A theme switch can leave a name the theme no longer provides, and the
    form posts it straight back. That must not block editing the rest."""
    page_id = publish_page(app, acme, slug='tmpl-stranded')
    page = db.session.get(Content, page_id)
    page.template = 'wide-legacy'
    db.session.add(page)
    db.session.commit()

    login_as(client, user)
    client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed', 'slug': 'tmpl-stranded', 'body': 'b',
                      'status': 'published', 'visibility': 'public',
                      'template': 'wide-legacy'}, follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).title == 'Renamed'


def _store_raw_template(page_id, value):
    """Write the column directly, as a row saved before the rule existed."""
    db.session.execute(db.text('UPDATE content SET template=:t WHERE id=:i'),
                       {'t': value, 'i': page_id})
    db.session.commit()
    db.session.expire_all()


@pytest.mark.parametrize('stored', [
    'manage/members',       # rendered the Manage console to anonymous visitors
    'manage/domains',
    'discussion-post',      # rendered community/, and 500ed for want of context
    'archive',
    '../../../etc/passwd',
])
def test_a_row_stored_before_the_rule_is_ignored_at_render(app, client, acme, stored):
    """The write-time guard cannot reach rows that already exist, so the read
    side has to refuse them too."""
    page_id = publish_page(app, acme, slug='legacy-tmpl')
    _store_raw_template(page_id, stored)

    response = client.get('/legacy-tmpl', base_url=ACME_HOST)
    assert response.status_code == 200
    body = response.data.decode()
    assert 'Manage —' not in body
    assert 'csrf_token' not in body
    assert 'Our story.' in body          # its own body, via the default template


def test_editing_a_legacy_row_clears_the_bad_value(app, client, acme, user):
    page_id = publish_page(app, acme, slug='legacy-heal')
    _store_raw_template(page_id, 'discussion-post')
    login_as(client, user)

    client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed', 'slug': 'legacy-heal', 'body': 'b',
                      'status': 'published', 'visibility': 'public',
                      'template': 'discussion-post'}, follow_redirects=True)
    db.session.expire_all()
    page = db.session.get(Content, page_id)
    assert page.template is None      # dropped, not refused
    assert page.title == 'Renamed'    # and the rest of the edit went through


def test_a_newly_typed_disallowed_template_is_refused(app, client, acme, user):
    page_id = publish_page(app, acme, slug='legacy-new')
    login_as(client, user)

    response = client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                           data={'title': 'T', 'slug': 'legacy-new', 'body': 'b',
                                 'status': 'published', 'visibility': 'public',
                                 'template': 'members'}, follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).template is None
    assert b'No template called' in response.data


def test_a_non_page_row_with_a_legacy_template_is_still_editable(app, client,
                                                                 acme, user):
    """validate() enforces the rule for every content type, but only the page
    editor has a Template field. Without the clear-up, an article carrying a
    legacy value could never be saved again and there was no way to fix it."""
    article = Content.query.filter_by(org_id=acme.id, type='article').first()
    article.status = 'published'
    db.session.add(article)
    db.session.commit()
    article_id, slug = article.id, article.slug
    _store_raw_template(article_id, 'manage/members')

    login_as(client, user)
    client.post(f'/manage/content/{article_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed Article', 'slug': slug, 'body': 'b',
                      'status': 'published', 'visibility': 'public'},
                follow_redirects=True)
    db.session.expire_all()
    row = db.session.get(Content, article_id)
    assert row.title == 'Renamed Article'
    assert row.template is None


def test_preview_ignores_a_legacy_template_too(app, client, acme, user):
    """Preview is the second reader of the same column."""
    page_id = publish_page(app, acme, slug='legacy-preview')
    _store_raw_template(page_id, 'manage/members')

    login_as(client, user)
    response = client.get(f'/manage/content/{page_id}/preview', base_url=ACME_HOST)
    assert response.status_code == 200
    assert b'Manage &mdash;' not in response.data
    assert 'Manage —' not in response.data.decode()
