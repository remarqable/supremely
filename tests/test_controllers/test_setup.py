"""First-run wizard: completion test for the whole flow (SQLite path)."""

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import InstallationSetting, Membership, Organization, User


@pytest.fixture
def fresh_app(tmp_path):
    class FreshConfig(TestConfig):
        SETUP_COMPLETE = False
        DATA_DIR = str(tmp_path)

    app = create_app(FreshConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def fresh_client(fresh_app):
    return fresh_app.test_client()


def run_wizard(client, org=True):
    client.post('/setup/environment', data={
        'name': 'My Community', 'base_url': 'http://example.test',
        'timezone': 'UTC', 'language': 'en'})
    client.post('/setup/database', data={'engine': 'sqlite'})
    client.post('/setup/admin', data={
        'email': 'admin@example.com', 'password': 'super-secret-1',
        'confirm_password': 'super-secret-1'})
    client.post('/setup/email', data={'skip': '1'})
    if org:
        return client.post('/setup/organization', data={
            'name': 'First Org', 'slug': 'first'})
    return client.post('/setup/organization', data={'skip': '1'})


def test_uninstalled_redirects_everything_to_setup(fresh_client):
    response = fresh_client.get('/')
    assert response.status_code == 302
    assert '/setup' in response.headers['Location']


def test_wizard_full_flow(fresh_app, fresh_client):
    response = run_wizard(fresh_client)
    assert response.status_code == 200
    assert b'Installation complete' in response.data

    admin = User.get_by_email('admin@example.com')
    assert admin is not None
    assert admin.is_platform_admin
    assert admin.check_password('super-secret-1')

    org = Organization.get_by_slug('first')
    assert org is not None
    assert Membership.get(admin.id, org.id).role == 'owner'

    assert InstallationSetting.get_value('installation.name') == 'My Community'
    assert not InstallationSetting.get_bool(
        'installation.allow_organization_signups')

    # Wizard is disabled after installation.
    assert fresh_client.get('/setup/').status_code == 404
    # Config was written to the data volume.
    from app.platform.config_store import read_runtime_config
    config = read_runtime_config(fresh_app)
    assert config['SETUP_COMPLETE'] == 'true'
    assert config['BASE_DOMAIN'] == 'example.test'
    assert 'SECRET_KEY' not in config or config['SECRET_KEY'] != 'test-secret'


def test_wizard_skip_org(fresh_app, fresh_client):
    response = run_wizard(fresh_client, org=False)
    assert response.status_code == 200
    assert Organization.query.count() == 0
    assert User.get_by_email('admin@example.com').is_platform_admin


def test_wizard_rejects_weak_admin_password(fresh_client):
    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    fresh_client.post('/setup/database', data={'engine': 'sqlite'})
    response = fresh_client.post('/setup/admin', data={
        'email': 'admin@example.com', 'password': 'short',
        'confirm_password': 'short'}, follow_redirects=True)
    assert b'at least' in response.data


def test_wizard_cannot_skip_ahead(fresh_client):
    response = fresh_client.post('/setup/organization', data={'skip': '1'},
                                 follow_redirects=True)
    assert b'earlier steps' in response.data
