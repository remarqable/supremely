from flask import g

from app.extensions import db
from app.models import Membership, NavigationItem, Page
from tests.conftest import login_as, make_user


def publish_page(app, org, slug='story', title='Our Story', body='Our story.',
                 visibility='public', **kwargs):
    with app.test_request_context():
        g.org = org
        page = Page(title=title, slug=slug, body=body, org_id=org.id,
                    visibility=visibility, **kwargs)
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
        Page(title='Secret', slug='secret', org_id=acme.id).save()
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


def test_member_only_page_redirects_anonymous(app, client, acme, globex):
    publish_page(app, acme, slug='inside', visibility='members')
    response = client.get('/inside', base_url=ACME)
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_member_only_page_visible_to_member(app, client, acme, globex, user):
    publish_page(app, acme, slug='inside', visibility='members')
    login_as(client, user)
    assert client.get('/inside', base_url=ACME).status_code == 200


def test_member_only_page_hidden_from_non_member(app, client, acme, globex):
    publish_page(app, acme, slug='inside', visibility='members')
    outsider = make_user(email='out@example.com')
    login_as(client, outsider)
    # Private org default would 404 the whole host; PUBLIC_TENANTS shows the
    # site but hides member content from non-members.
    assert client.get('/inside', base_url=ACME).status_code == 404


def test_homepage_designation(app, client, acme, globex):
    page_id = publish_page(app, acme, slug='welcome', title='Welcome',
                           body='This is the homepage content.')
    acme.update_settings(homepage_page_id=page_id)
    response = client.get('/', base_url=ACME)
    assert b'This is the homepage content.' in response.data


def test_navigation_rendered(app, client, acme, globex):
    page_id = publish_page(app, acme)
    with app.test_request_context():
        g.org = acme
        NavigationItem(menu='primary', label='Story', page_id=page_id,
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
