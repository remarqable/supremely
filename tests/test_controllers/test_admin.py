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


def test_email_opens_on_the_provider_in_use(admin_client):
    assert admin_client.get('/admin/email').headers['Location'].endswith(
        '/admin/email/custom')
    admin_client.post('/admin/email/gmail', data={
        'smtp_username': 'someone@gmail.com', 'smtp_password': 'app-password',
        'from_address': 'someone@gmail.com'})
    assert admin_client.get('/admin/email').headers['Location'].endswith(
        '/admin/email/gmail')


def test_each_provider_has_a_page_asking_only_for_its_own_fields(admin_client):
    """The point of the split: no page shows a field belonging to a provider
    the operator is not setting up."""
    custom = admin_client.get('/admin/email/custom').get_data(as_text=True)
    assert 'name="smtp_host"' in custom
    assert 'name="mailgun_api_key"' not in custom

    gmail = admin_client.get('/admin/email/gmail').get_data(as_text=True)
    assert 'name="smtp_username"' in gmail
    assert 'name="smtp_host"' not in gmail       # the preset knows the host
    assert 'name="mailgun_api_key"' not in gmail

    mailgun = admin_client.get('/admin/email/mailgun').get_data(as_text=True)
    assert 'name="mailgun_api_key"' in mailgun
    assert 'name="mailgun_domain"' in mailgun
    assert 'name="smtp_host"' not in mailgun
    assert 'name="smtp_password"' not in mailgun


def test_every_provider_is_reachable_from_the_nav(admin_client):
    page = admin_client.get('/admin/email/custom').get_data(as_text=True)
    for path in ('/admin/email/custom', '/admin/email/gmail',
                 '/admin/email/mailgun'):
        assert path in page, path


def test_a_provider_nobody_wrote_is_not_a_page(admin_client):
    assert admin_client.get('/admin/email/sendgrid').status_code == 404
    assert admin_client.post('/admin/email/sendgrid', data={
        'from_address': 'news@example.com'}).status_code == 404
    assert InstallationSetting.query.filter_by(key='email.provider').first() is None


def test_the_email_section_left_the_settings_page(admin_client):
    """It used to be a second card there, which is where the duplicate came
    from. Settings keeps the general section only."""
    page = admin_client.get('/admin/settings').get_data(as_text=True)
    assert 'name="smtp_host"' not in page
    assert 'name="mailgun_api_key"' not in page
    assert page.count('name="name"') == 1


def test_choosing_gmail_asks_for_no_host(admin_client):
    """Naming a provider is worth nothing if the operator still has to know
    the host name. Gmail stores credentials and nothing else."""
    admin_client.post('/admin/email/gmail', data={
        'smtp_username': 'someone@gmail.com', 'smtp_password': 'app-password',
        'from_address': 'someone@gmail.com',
    })
    assert InstallationSetting.get_value('email.provider') == 'gmail'
    from app.platform.mailer import email_settings, is_email_configured, smtp_target
    assert is_email_configured()
    assert smtp_target(email_settings()) == ('smtp.gmail.com', 587, True)


def test_switching_provider_keeps_what_the_other_one_had(admin_client):
    """Trying Gmail and going back must not cost the SMTP host somebody
    typed, so only the chosen provider's own fields are written."""
    admin_client.post('/admin/email/custom', data={
        'smtp_host': 'smtp.example.com', 'smtp_port': '2525',
        'smtp_username': 'postmaster', 'smtp_password': 'secret',
        'from_address': 'news@example.com', 'use_tls': 'on',
    })
    admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': 'key-abc', 'mailgun_domain': 'mg.example.com',
        'mailgun_region': 'us', 'from_address': 'news@example.com',
    })
    assert InstallationSetting.get_value('email.smtp_host') == 'smtp.example.com'
    assert InstallationSetting.get_value('email.smtp_port') == '2525'


def test_a_secret_left_blank_is_kept_not_cleared(admin_client):
    """The stored key is never echoed into the form, so blank means the
    admin did not retype it."""
    admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': 'key-abc', 'mailgun_domain': 'mg.example.com',
        'mailgun_region': 'us', 'from_address': 'news@example.com',
    })
    admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': '', 'mailgun_domain': 'mg2.example.com',
        'mailgun_region': 'eu', 'from_address': 'news@example.com',
    })
    assert InstallationSetting.get_value('email.mailgun_api_key') == 'key-abc'
    assert InstallationSetting.get_value('email.mailgun_domain') == 'mg2.example.com'


def test_a_secret_is_never_written_into_the_page(admin_client):
    admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': 'key-super-secret',
        'mailgun_domain': 'mg.example.com', 'mailgun_region': 'us',
        'from_address': 'news@example.com',
    })
    page = admin_client.get('/admin/email/mailgun').get_data(as_text=True)
    assert 'key-super-secret' not in page


def test_an_unusable_sending_domain_is_refused_at_the_form(admin_client):
    """Said now, rather than discovered by a newsletter that will not send."""
    response = admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': 'key-abc',
        'mailgun_domain': 'https://evil.test', 'mailgun_region': 'us',
        'from_address': 'news@example.com',
    }, follow_redirects=True)
    assert b'is not a sending domain' in response.data
    # Nothing at all was written: each setting commits as it is set, so a
    # refusal part way would leave a provider selected that cannot send.
    assert InstallationSetting.query.filter_by(key='email.provider').first() is None
    assert InstallationSetting.query.filter_by(
        key='email.mailgun_domain').first() is None


def test_a_port_that_is_not_one_is_refused(admin_client):
    """Caught at the form, rather than by the first newsletter that will not
    go out. Nothing is written, so the installation is not left selecting a
    provider it cannot send through."""
    response = admin_client.post('/admin/email/custom', data={
        'smtp_host': 'smtp.example.com', 'smtp_port': 'not-a-port',
        'from_address': 'news@example.com'}, follow_redirects=True)
    assert b'is not a port number' in response.data
    assert InstallationSetting.query.filter_by(key='email.provider').first() is None

    from app.platform.mailer import is_email_configured
    assert not is_email_configured()


def test_a_from_address_that_is_not_one_is_refused(admin_client):
    """is_email_configured treats a non-empty from address as enough, so a
    value that is not an address would read as configured and fail on every
    send. A newline in it is header injection."""
    from app.platform.mailer import is_email_configured
    for bad in ('not-an-address', 'news@example.com\nBcc: x@y.test', ''):
        response = admin_client.post('/admin/email/custom', data={
            'smtp_host': 'smtp.example.com', 'from_address': bad},
            follow_redirects=True)
        assert b'is not an email address' in response.data, bad
        assert InstallationSetting.query.filter_by(
            key='email.provider').first() is None, bad
    assert not is_email_configured()


def test_custom_and_gmail_share_the_smtp_login(admin_client):
    """They are the same field: an SMTP username and password. Saving one
    overwrites the other, and the documentation says so rather than
    promising an isolation that is not there. Mailgun's own credentials are
    untouched either way."""
    admin_client.post('/admin/email/mailgun', data={
        'mailgun_api_key': 'key-abc', 'mailgun_domain': 'mg.example.com',
        'mailgun_region': 'us', 'from_address': 'news@example.com'})
    admin_client.post('/admin/email/custom', data={
        'smtp_host': 'smtp.example.com', 'smtp_username': 'postmaster',
        'smtp_password': 'smtp-secret', 'from_address': 'news@example.com'})
    admin_client.post('/admin/email/gmail', data={
        'smtp_username': 'someone@gmail.com', 'smtp_password': 'gmail-app-pw',
        'from_address': 'someone@gmail.com'})

    assert InstallationSetting.get_value('email.smtp_username') == 'someone@gmail.com'
    assert InstallationSetting.get_value('email.smtp_host') == 'smtp.example.com'
    assert InstallationSetting.get_value('email.mailgun_api_key') == 'key-abc'


def test_the_test_send_returns_to_the_provider_it_tested(admin_client):
    """It used to land on the general Settings page, which has no email on
    it at all, so the result of the test appeared somewhere unrelated."""
    admin_client.post('/admin/email/gmail', data={
        'smtp_username': 'someone@gmail.com', 'smtp_password': 'app-password',
        'from_address': 'someone@gmail.com'})
    response = admin_client.post('/admin/settings/test-email',
                                 data={'to': 'someone@example.com'})
    assert response.headers['Location'].endswith('/admin/email/gmail')

    # And with no address to send to, which is the other way out.
    assert admin_client.post('/admin/settings/test-email', data={'to': ''}) \
        .headers['Location'].endswith('/admin/email/gmail')


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
