from tests.conftest import login_as


def test_default_org_mode_single_org(client, acme):
    """Bare domain serves the sole organization directly."""
    response = client.get('/', base_url='http://example.test')
    assert response.status_code == 200
    assert b'Acme' in response.data


def test_bare_domain_with_multiple_orgs_is_installation(client, acme, globex):
    response = client.get('/', base_url='http://example.test')
    assert response.status_code == 200
    assert b'Acme' not in response.data
    assert b'Globex' not in response.data


def test_subdomain_resolves_org(client, acme, globex):
    response = client.get('/', base_url='http://globex.example.test')
    assert response.status_code == 200
    assert b'Globex' in response.data


def test_unknown_subdomain_404(client, acme, globex):
    response = client.get('/', base_url='http://nope.example.test')
    assert response.status_code == 404


def test_suspended_org_410(client, acme, globex):
    globex.suspend()
    response = client.get('/', base_url='http://globex.example.test')
    assert response.status_code == 410


def test_admin_paths_bypass_tenant(client, acme, platform_admin):
    login_as(client, platform_admin)
    response = client.get('/admin/', base_url='http://example.test')
    assert response.status_code == 200


def test_health_always_available(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'


def test_org_dashboard_requires_membership(client, acme, globex, user):
    """user owns acme, is not in globex: globex dashboard is a 404."""
    login_as(client, user)
    ok = client.get('/dashboard', base_url='http://acme.example.test')
    assert ok.status_code == 200
    denied = client.get('/dashboard', base_url='http://globex.example.test')
    assert denied.status_code == 404
