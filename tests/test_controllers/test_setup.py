"""First-run wizard: completion test for the whole flow.

The wizard has three steps. It does not ask for a database engine (config
resolves that before boot) and it does not ask for SMTP (Administration ->
Settings owns that). The platform admin is always the username "admin".
"""

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
    client.post('/setup/admin', data={
        'password': 'super-secret-1', 'confirm_password': 'super-secret-1'})
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

    admin = User.get_by_email('admin')
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
    assert User.get_by_email('admin').is_platform_admin


def test_wizard_rejects_weak_admin_password(fresh_client):
    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    response = fresh_client.post('/setup/admin', data={
        'password': 'short', 'confirm_password': 'short'},
        follow_redirects=True)
    assert b'at least' in response.data


def test_wizard_cannot_skip_ahead(fresh_client):
    response = fresh_client.post('/setup/organization', data={'skip': '1'},
                                 follow_redirects=True)
    assert b'earlier steps' in response.data


def test_admin_username_is_fixed_and_not_taken_from_the_form(fresh_client):
    """The username is not a form field. Posting one must not change it."""
    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    fresh_client.post('/setup/admin', data={
        'email': 'attacker@example.com', 'username': 'attacker',
        'password': 'super-secret-1', 'confirm_password': 'super-secret-1'})
    fresh_client.post('/setup/organization', data={'skip': '1'})

    assert User.get_by_email('admin') is not None
    assert User.get_by_email('attacker@example.com') is None
    assert User.query.count() == 1


def test_removed_steps_are_gone(fresh_client):
    for path in ('/setup/database', '/setup/email'):
        assert fresh_client.get(path).status_code == 404


def test_organization_step_is_prefilled(fresh_client):
    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    fresh_client.post('/setup/admin', data={
        'password': 'super-secret-1', 'confirm_password': 'super-secret-1'})
    body = fresh_client.get('/setup/organization').data
    assert b'Our community' in body
    assert b'our-community' in body


def test_organization_name_longer_than_the_column_is_rejected(fresh_app, fresh_client):
    """The controller used to re-state the org rules and omitted the length
    check, so an over-length name reached the column: silent truncation risk
    on SQLite, a failed install mid-write on PostgreSQL."""
    from app.models import Organization

    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    fresh_client.post('/setup/admin', data={
        'password': 'super-secret-1', 'confirm_password': 'super-secret-1'})
    response = fresh_client.post('/setup/organization', data={
        'name': 'N' * 250, 'slug': 'ok-slug'})

    assert b'too long' in response.data
    assert Organization.query.count() == 0

    # The install is still completable afterwards.
    done = fresh_client.post('/setup/organization', data={
        'name': 'Our community', 'slug': 'our-community'})
    assert b'Installation complete' in done.data
    assert Organization.get_by_slug('our-community') is not None


def test_reserved_slug_is_rejected(fresh_client):
    fresh_client.post('/setup/environment', data={
        'name': 'X', 'base_url': 'http://example.test'})
    fresh_client.post('/setup/admin', data={
        'password': 'super-secret-1', 'confirm_password': 'super-secret-1'})
    response = fresh_client.post('/setup/organization', data={
        'name': 'Admin', 'slug': 'admin'})
    assert b'reserved' in response.data
