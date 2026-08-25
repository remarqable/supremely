from app.models import InstallationSetting, Membership, Organization, User
from tests.conftest import login_as


def test_admin_requires_platform_admin(client, user):
    login_as(client, user)
    assert client.get('/admin/').status_code == 404      # not 403: don't confirm


def test_admin_requires_login(client, app):
    response = client.get('/admin/')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_dashboard(admin_client, acme):
    response = admin_client.get('/admin/')
    assert response.status_code == 200
    assert b'Acme' in response.data


def test_create_organization(admin_client, platform_admin):
    response = admin_client.post('/admin/orgs/new', data={
        'name': 'New Org', 'slug': 'new-org', 'owner_id': platform_admin.id,
    })
    assert response.status_code == 302
    org = Organization.get_by_slug('new-org')
    assert org is not None
    assert Membership.get(platform_admin.id, org.id).role == 'owner'


def test_create_user(admin_client):
    response = admin_client.post('/admin/users/new', data={
        'email': 'fresh@example.com', 'name': 'Fresh',
        'password': 'a-fine-password-1',
    })
    assert response.status_code == 302
    user = User.get_by_email('fresh@example.com')
    assert user is not None
    assert user.check_password('a-fine-password-1')


def test_assign_user_to_org(admin_client, acme, platform_admin):
    admin_client.post('/admin/users/new', data={
        'email': 'assignee@example.com', 'name': 'A',
        'password': 'a-fine-password-1'})
    user = User.get_by_email('assignee@example.com')
    response = admin_client.post(f'/admin/orgs/{acme.id}/members', data={
        'email': user.email, 'role': 'member',
    })
    assert response.status_code == 302
    assert Membership.get(user.id, acme.id).role == 'member'


def test_suspend_reactivate_org(admin_client, acme):
    admin_client.post(f'/admin/orgs/{acme.id}/suspend')
    assert not Organization.get_by_id(acme.id).is_active
    admin_client.post(f'/admin/orgs/{acme.id}/reactivate')
    assert Organization.get_by_id(acme.id).is_active


def test_settings_signup_toggle(admin_client):
    admin_client.post('/admin/settings', data={
        'section': 'general', 'name': 'My Install', 'allow_signups': 'on',
        'timezone': 'UTC', 'language': 'en',
    })
    assert InstallationSetting.get_bool('installation.allow_organization_signups')
    assert InstallationSetting.get_value('installation.name') == 'My Install'


def test_cannot_demote_self(admin_client, platform_admin):
    admin_client.post(f'/admin/users/{platform_admin.id}/toggle-admin')
    assert User.get_by_id(platform_admin.id).is_platform_admin


def test_system_page(admin_client, app):
    response = admin_client.get('/admin/system')
    assert response.status_code == 200
    engine = b'postgresql' if app.config['IS_POSTGRES'] else b'sqlite'
    assert engine in response.data.lower()
