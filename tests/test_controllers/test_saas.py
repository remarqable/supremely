"""Phase 8: hosted SaaS readiness. Signup flow, custom domains, expanded
platform administration."""

from app.extensions import db
from app.models import InstallationSetting, Membership, Organization, OrgDomain, User
from tests.conftest import login_as, make_user

BARE = 'http://example.test'


def enable_signups(app):
    InstallationSetting.set('installation.allow_organization_signups', 'true')


# --- Public signup flow ------------------------------------------------------------

def test_register_404_when_signups_disabled(client, app):
    assert client.get('/auth/register', base_url=BARE).status_code == 404


def test_full_signup_flow(app, client, acme, globex):
    """Register -> create organization -> choose slug -> owner -> enter."""
    enable_signups(app)

    page = client.get('/auth/register', base_url=BARE)
    assert page.status_code == 200

    response = client.post('/auth/register', base_url=BARE, data={
        'name': 'Founder', 'email': 'founder@example.com',
        'password': 'founder-secret-1'})
    assert response.status_code == 302
    assert '/launcher/new' in response.headers['Location']

    response = client.post('/launcher/new', base_url=BARE, data={
        'name': 'My Startup', 'slug': 'my-startup'})
    assert response.status_code == 302
    assert 'my-startup.example.test' in response.headers['Location']

    founder = User.get_by_email('founder@example.com')
    org = Organization.get_by_slug('my-startup')
    assert Membership.get(founder.id, org.id).role == 'owner'

    # Enter the organization on its subdomain
    home = client.get('/', base_url='http://my-startup.example.test')
    assert home.status_code == 200
    assert b'My Startup' in home.data


def test_register_duplicate_email(app, client, user):
    enable_signups(app)
    response = client.post('/auth/register', base_url=BARE, data={
        'name': 'X', 'email': user.email, 'password': 'whatever-123'})
    assert response.status_code == 400
    assert b'create that account' in response.data


def test_login_page_links_register_only_when_enabled(app, client):
    page = client.get('/auth/login', base_url=BARE)
    assert b'/auth/register' not in page.data
    enable_signups(app)
    page = client.get('/auth/login', base_url=BARE)
    assert b'/auth/register' in page.data


# --- Custom domains -----------------------------------------------------------------

def test_org_adds_domain_admin_activates(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/domains',
                           base_url='http://acme.example.test',
                           data={'domain': 'community.acme-corp.com'},
                           follow_redirects=True)
    assert b'awaiting activation' in response.data
    domain = OrgDomain.query.first()
    assert domain.status == 'pending'

    # Pending domains do not resolve
    assert client.get('/', base_url='http://community.acme-corp.com'
                      ).status_code == 404

    # Platform admin activates
    admin = make_user(email='root2@example.com', is_platform_admin=True)
    admin_client = app.test_client()
    login_as(admin_client, admin)
    admin_client.post(f'/admin/domains/{domain.id}/activate')
    assert db.session.get(OrgDomain, domain.id).status == 'active'

    # The custom domain now serves the organization
    home = app.test_client().get('/', base_url='http://community.acme-corp.com')
    assert home.status_code == 200
    assert b'Acme' in home.data


def test_unknown_foreign_domain_404(client, acme, globex):
    assert client.get('/', base_url='http://random-stranger.com'
                      ).status_code == 404


def test_domain_validation(app, client, acme, globex, user):
    login_as(client, user)
    base = 'http://acme.example.test'
    # Subdomains of the installation are automatic, not claimable
    response = client.post('/manage/domains', base_url=base,
                           data={'domain': 'evil.example.test'},
                           follow_redirects=True)
    assert b'automatic' in response.data
    response = client.post('/manage/domains', base_url=base,
                           data={'domain': 'not a domain'},
                           follow_redirects=True)
    assert b'valid domain' in response.data
    assert OrgDomain.query.count() == 0


def test_domain_unique_across_orgs(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/domains', base_url='http://acme.example.test',
                data={'domain': 'shared.example.org'})

    globex_owner = make_user(email='gxo@example.com')
    Membership.add(globex_owner.id, globex.id, role='owner')
    globex_client = app.test_client()
    login_as(globex_client, globex_owner)
    response = globex_client.post('/manage/domains',
                                  base_url='http://globex.example.test',
                                  data={'domain': 'shared.example.org'},
                                  follow_redirects=True)
    assert b'already claimed' in response.data
    assert OrgDomain.query.count() == 1


def test_member_cannot_manage_domains(app, client, acme, globex):
    member = make_user(email='dm@example.com')
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/manage/domains',
                      base_url='http://acme.example.test').status_code == 403


# --- Expanded platform administration -------------------------------------------------

def test_admin_org_detail_shows_state(admin_client, app, acme, globex, user):
    from flask import g

    from app.models import Content
    with app.test_request_context():
        g.org = acme
        page = Content(type='page', title='P', slug='p', org_id=acme.id, fields={}, tags=[])
        page.save()

    detail = admin_client.get(f'/admin/orgs/{acme.id}')
    assert detail.status_code == 200
    assert b'origin' in detail.data             # theme state
    for label in (b'Members', b'Pages', b'Posts', b'Discussions', b'Subscribers'):
        assert label in detail.data             # usage indicators
