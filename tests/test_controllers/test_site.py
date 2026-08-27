from flask import g

from app.extensions import db
from app.models import Content, NavigationItem
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
                          excerpt=body[:80], org_id=org.id,
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
    response = client.post('/manage/settings', base_url=ACME,
                           data={'section': 'privacy'})   # checkbox absent
    assert response.status_code == 302
    assert acme.teases_gated_content() is False
    client.post('/manage/settings', base_url=ACME,
                data={'section': 'privacy', 'gated_teasers': 'on'})
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
