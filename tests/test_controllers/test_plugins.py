"""Phase 7: plugin ecosystem. Completion test: vertical functionality (the
glossary reference plugin) works end to end without editing Supremely core."""

from flask import g

from app.extensions import db
from app.models import Membership, OrgPlugin, Post
from app.platform.plugins import MANIFESTS, REGISTRY, installed_version
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'


def install_glossary(client, base=ACME):
    return client.post('/manage/plugins/glossary/install', base_url=base)


def test_registry_loaded(app):
    assert 'glossary' in MANIFESTS
    assert '1' in REGISTRY['glossary']


def test_not_installed_is_404(client, acme, globex, user):
    login_as(client, user)
    assert client.get('/glossary/', base_url=ACME).status_code == 404
    assert client.get('/glossary', base_url=ACME).status_code in (301, 308, 404)


def test_install_enables_routes_and_seeds(app, client, acme, globex, user):
    login_as(client, user)
    response = install_glossary(client)
    assert response.status_code == 302

    page = client.get('/glossary/', base_url=ACME)
    assert page.status_code == 200
    assert b'Supremely' in page.data        # on_install seeded a first term
    # The private mount never leaks into rendered pages
    assert b'/_v/' not in page.data

    row = OrgPlugin.query.filter_by(org_id=acme.id).first()
    assert row.plugin_slug == 'glossary'
    assert row.version == '1'
    assert row.is_enabled


def test_plugin_nav_appears_when_installed(app, client, acme, globex, user):
    login_as(client, user)
    home_before = client.get('/', base_url=ACME)
    assert b'/glossary' not in home_before.data
    install_glossary(client)
    home_after = client.get('/', base_url=ACME)
    assert b'href="/glossary"' in home_after.data


def test_add_and_search_terms(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    client.post('/glossary/', base_url=ACME, data={
        'term': 'Tenant', 'definition': 'An organization on the installation.'})
    page = client.get('/glossary/?q=tenant', base_url=ACME)
    assert b'An organization on the installation.' in page.data
    assert b'Supremely' not in page.data    # filtered out by search


def test_member_reads_but_cannot_write(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    member = make_user(email='reader@example.com')
    Membership.add(member.id, acme.id, role='member')
    member_client = app.test_client()
    login_as(member_client, member)

    assert member_client.get('/glossary/', base_url=ACME).status_code == 200
    response = member_client.post('/glossary/', base_url=ACME, data={
        'term': 'Nope', 'definition': 'no'})
    assert response.status_code == 403


def test_settings_apply(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    client.post('/manage/plugins/glossary/settings', base_url=ACME,
                data={'setting_headline': 'Company Dictionary'})
    page = client.get('/glossary/', base_url=ACME)
    assert b'Company Dictionary' in page.data


def test_plugin_post_type_registered(app, client, acme, globex, user):
    """The plugin contributes a structured Post Type usable in the editor."""
    from app.platform.post_types import POST_TYPES
    assert 'definition' in POST_TYPES
    assert POST_TYPES['definition'].plugin == 'glossary'

    login_as(client, user)
    client.post('/manage/posts/new?type=definition', base_url=ACME, data={
        'title': 'What is a Widget', 'slug': 'what-is-a-widget',
        'body': 'A widget is a thing.', 'visibility': 'public',
        'field_term': 'Widget', 'field_pronunciation': 'WIH-jit',
        'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Post.published_by_slug('what-is-a-widget')
        assert post.fields == {'term': 'Widget', 'pronunciation': 'WIH-jit'}


def test_disable_keeps_data(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    client.post('/glossary/', base_url=ACME, data={
        'term': 'Keeper', 'definition': 'Survives disable.'})

    client.post('/manage/plugins/glossary/uninstall', base_url=ACME)
    assert client.get('/glossary/', base_url=ACME).status_code == 404
    home = client.get('/', base_url=ACME)
    assert b'href="/glossary"' not in home.data

    # Re-enable: the tenant's data is intact (uninstall disables, never deletes)
    install_glossary(client)
    page = client.get('/glossary/', base_url=ACME)
    assert b'Keeper' in page.data
    assert b'Survives disable.' in page.data


def test_plugin_tenant_isolation(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    client.post('/glossary/', base_url=ACME, data={
        'term': 'AcmeOnly', 'definition': 'secret'})

    # Globex never installed it: route does not exist there
    globex_owner = make_user(email='gx@example.com')
    Membership.add(globex_owner.id, globex.id, role='owner')
    globex_client = app.test_client()
    login_as(globex_client, globex_owner)
    assert globex_client.get('/glossary/', base_url=GLOBEX).status_code == 404

    # Globex installs it: own seeded data only, never acme's
    globex_client.post('/manage/plugins/glossary/install', base_url=GLOBEX)
    page = globex_client.get('/glossary/', base_url=GLOBEX)
    assert page.status_code == 200
    assert b'AcmeOnly' not in page.data


def test_member_cannot_manage_plugins(app, client, acme, globex, user):
    member = make_user(email='pm@example.com')
    Membership.add(member.id, acme.id, role='member')
    member_client = app.test_client()
    login_as(member_client, member)
    assert member_client.get('/manage/plugins',
                             base_url=ACME).status_code == 403
    assert member_client.post('/manage/plugins/glossary/install',
                              base_url=ACME).status_code == 403


def test_installed_version_memoised_per_request(app, client, acme, globex, user):
    login_as(client, user)
    install_glossary(client)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert installed_version('glossary') == '1'
        assert installed_version('nonexistent') is None
