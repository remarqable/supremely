from app.extensions import db
from app.models import (
    InstallationSetting,
    Job,
    Membership,
    Organization,
    User,
)
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

# --- Failed jobs (#42) -------------------------------------------------------

def _make_failed_job(name='test.explode', org_id=None):
    """A terminally failed job, produced by actually failing rather than by
    writing the row, so the test exercises the worker's own bookkeeping."""
    from app.platform.jobs import enqueue, job, run_pending_jobs

    @job(name)
    def explode(payload):
        raise RuntimeError('kaboom')

    enqueue(name, org_id=org_id, max_attempts=1)
    run_pending_jobs()
    return Job.query.filter_by(name=name).first()


def test_failed_job_is_listed_with_its_error(admin_client, app):
    _make_failed_job()
    response = admin_client.get('/admin/jobs')
    assert response.status_code == 200
    assert b'test.explode' in response.data
    assert b'RuntimeError: kaboom' in response.data


def test_failed_job_names_its_organization(admin_client, acme):
    _make_failed_job(org_id=acme.id)
    assert b'Acme' in admin_client.get('/admin/jobs').data


def test_jobs_page_hidden_from_an_org_owner(client, acme, user):
    login_as(client, user)                  # owner of acme, not a platform admin
    assert client.get('/admin/jobs').status_code == 404
    assert client.post('/admin/jobs/1/retry').status_code == 404


def test_jobs_page_requires_login(client, app):
    assert client.get('/admin/jobs').status_code == 302


def test_retry_requeues_and_reruns_the_handler(admin_client, app):
    from app.platform.jobs import run_pending_jobs
    row = _make_failed_job()
    job_id = row.id

    admin_client.post(f'/admin/jobs/{job_id}/retry')
    requeued = db.session.get(Job, job_id)
    assert requeued.status == 'pending'
    assert requeued.attempts == 0
    assert requeued.last_error is None

    run_pending_jobs()                      # the handler still raises
    assert db.session.get(Job, job_id).status == 'failed'
    assert db.session.get(Job, job_id).attempts == 1


def test_retry_refuses_a_job_that_did_not_fail(admin_client, app):
    row = Job(name='x', status='pending')
    db.session.add(row)
    db.session.commit()
    admin_client.post(f'/admin/jobs/{row.id}/retry')
    assert db.session.get(Job, row.id).status == 'pending'
    assert db.session.get(Job, row.id).attempts == 0


def test_retry_of_a_missing_job_is_404(admin_client, app):
    assert admin_client.post('/admin/jobs/999999/retry').status_code == 404
