from app.models import InstallationSetting, Organization
from tests.conftest import login_as, make_user


def test_zero_orgs_shows_onboarding(client, app):
    user = make_user()
    login_as(client, user)
    response = client.get('/launcher')
    assert response.status_code == 200
    assert b'do not belong' in response.data


def test_single_org_redirects(client, acme, user):
    login_as(client, user)
    response = client.get('/launcher')
    assert response.status_code == 302


def test_multiple_orgs_shows_launcher(client, acme, globex, user):
    from app.models import Membership
    Membership.add(user.id, globex.id, role='member')
    login_as(client, user)
    response = client.get('/launcher')
    assert response.status_code == 200
    assert b'Acme' in response.data
    assert b'Globex' in response.data


def test_launcher_requires_login(client, app):
    response = client.get('/launcher')
    assert response.status_code == 302


def test_org_creation_needs_permission(client, app):
    user = make_user()
    login_as(client, user)
    assert client.get('/launcher/new').status_code == 403

    InstallationSetting.set('installation.allow_organization_signups', 'true')
    assert client.get('/launcher/new').status_code == 200

    response = client.post('/launcher/new', data={
        'name': 'Mine', 'slug': 'mine',
    })
    assert response.status_code == 302
    org = Organization.get_by_slug('mine')
    assert org is not None
    from app.models import Membership
    assert Membership.get(user.id, org.id).role == 'owner'


def test_platform_admin_can_always_create(client, platform_admin):
    login_as(client, platform_admin)
    assert client.get('/launcher/new').status_code == 200
