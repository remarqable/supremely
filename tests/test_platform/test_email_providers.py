"""Named email providers: what each one needs, and how each one sends.

Every provider here is reachable through Custom SMTP by hand. Naming them
is a matter of not making an operator find Gmail's host name or learn that
Mailgun's API wants HTTP Basic with the literal username "api".
"""

import base64
import json
import socket
import urllib.error
from io import BytesIO

import pytest

from app.models import InstallationSetting
from app.platform import mailer
from app.platform.errors import (
    EmailNotConfiguredError,
    EmailSendError,
    ValidationError,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """These tests turn MAIL_SUPPRESS_SEND off to exercise the transports,
    which removes the guard the rest of the suite relies on. Nothing here
    may reach the internet, so the socket itself refuses rather than each
    test being trusted to remember its own patch. Written after a patched
    urlopen stopped intercepting and the suite got a real 401 from Mailgun.
    """
    def refuse(*args, **kwargs):
        raise AssertionError('the test suite opened a network connection')

    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


def configure(app, **settings):
    for key, value in settings.items():
        InstallationSetting.set(f'email.{key}', value)
    app.config['MAIL_SUPPRESS_SEND'] = False
    mailer._outbox.clear()


MAILGUN = {'provider': 'mailgun', 'mailgun_api_key': 'key-abc',
           'mailgun_domain': 'mg.example.com', 'from_address': 'news@example.com'}
GMAIL = {'provider': 'gmail', 'smtp_username': 'someone@gmail.com',
         'smtp_password': 'app-password', 'from_address': 'someone@gmail.com'}
CUSTOM = {'provider': 'custom', 'smtp_host': 'smtp.example.com',
          'from_address': 'news@example.com'}


class FakeResponse:
    def __init__(self, body=b'{"id": "<queued>"}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_each_provider_knows_when_it_is_configured(app):
    with app.app_context():
        assert not mailer.is_email_configured()

        # Custom wants a host; Gmail knows the host and wants credentials.
        configure(app, **CUSTOM)
        assert mailer.is_email_configured()
        InstallationSetting.set('email.smtp_host', '')
        assert not mailer.is_email_configured()

        configure(app, **GMAIL)
        assert mailer.is_email_configured()   # no host asked for, or needed
        InstallationSetting.set('email.smtp_password', '')
        assert not mailer.is_email_configured()

        configure(app, **MAILGUN)
        assert mailer.is_email_configured()
        InstallationSetting.set('email.mailgun_domain', '')
        assert not mailer.is_email_configured()


def test_gmail_needs_no_host_because_the_preset_has_one(app):
    """The point of naming a provider: the operator does not type this."""
    with app.app_context():
        configure(app, **GMAIL)
        assert mailer.smtp_target(mailer.email_settings()) == \
            ('smtp.gmail.com', 587, True)


def test_a_preset_is_not_stored_so_switching_back_keeps_what_was_typed(app):
    with app.app_context():
        configure(app, **CUSTOM, smtp_port='2525')
        InstallationSetting.set('email.provider', 'gmail')
        assert mailer.smtp_target(mailer.email_settings())[0] == 'smtp.gmail.com'
        InstallationSetting.set('email.provider', 'custom')
        assert mailer.smtp_target(mailer.email_settings()) == \
            ('smtp.example.com', 2525, True)


def test_an_unknown_provider_falls_back_rather_than_failing(app):
    with app.app_context():
        configure(app, **CUSTOM)
        InstallationSetting.set('email.provider', 'sendgrid-someday')
        assert mailer.email_provider() == 'custom'


def test_mailgun_posts_to_its_api_with_the_message(app, monkeypatch):
    with app.app_context():
        configure(app, **MAILGUN)
        sent = {}

        def fake_open(request, timeout=None):
            sent['url'] = request.full_url
            sent['auth'] = request.get_header('Authorization')
            sent['body'] = request.data.decode()
            sent['timeout'] = timeout
            return FakeResponse()

        monkeypatch.setattr(mailer._mailgun_opener, 'open', fake_open)
        mailer.send_email('reader@example.com', 'Hello', 'Body text.',
                          html='<p>Body text.</p>', attribution=False)

    assert sent['url'] == 'https://api.mailgun.net/v3/mg.example.com/messages'
    # Mailgun authenticates as the literal user "api" with the key.
    assert sent['auth'] == 'Basic ' + base64.b64encode(
        b'api:key-abc').decode()
    assert 'to=reader%40example.com' in sent['body']
    assert 'subject=Hello' in sent['body']
    assert 'html=' in sent['body']
    assert sent['timeout'] == 20


def test_mailgun_eu_is_a_different_host(app):
    with app.app_context():
        configure(app, **MAILGUN, mailgun_region='eu')
        assert mailer.mailgun_endpoint(mailer.email_settings()) == \
            'https://api.eu.mailgun.net/v3/mg.example.com/messages'
        # Anything that is not a region is the default one, not a new host.
        InstallationSetting.set('email.mailgun_region', 'https://evil.test')
        assert mailer.mailgun_endpoint(mailer.email_settings()).startswith(
            'https://api.mailgun.net/')


@pytest.mark.parametrize('domain', [
    '', '../../etc', 'evil.test/../..', 'mg.example.com/x',
    'https://evil.test', 'localhost', 'mg example.com',
])
def test_a_sending_domain_that_is_not_one_is_refused(app, domain):
    """The domain lands in a URL path, so it is checked rather than trusted."""
    with app.app_context():
        configure(app, **MAILGUN)
        InstallationSetting.set('email.mailgun_domain', domain)
        with pytest.raises(ValidationError):
            mailer.mailgun_endpoint(mailer.email_settings())


def test_mailgun_reports_its_own_refusal(app, monkeypatch):
    """A wrong key should say so, not read as a generic failure."""
    with app.app_context():
        configure(app, **MAILGUN)

        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, 'Unauthorized', {},
                BytesIO(json.dumps({'message': 'Invalid private key'}).encode()))

        monkeypatch.setattr(mailer._mailgun_opener, 'open', refuse)
        with pytest.raises(EmailSendError) as caught:
            mailer.send_email('reader@example.com', 'Hi', 'Body.')
        assert 'Invalid private key' in caught.value.message
        assert '401' in caught.value.message


def test_mailgun_unreachable_is_reported_not_raised_raw(app, monkeypatch):
    with app.app_context():
        configure(app, **MAILGUN)

        def unreachable(request, timeout=None):
            raise OSError('Name or service not known')

        monkeypatch.setattr(mailer._mailgun_opener, 'open', unreachable)
        with pytest.raises(EmailSendError) as caught:
            mailer.send_email('reader@example.com', 'Hi', 'Body.')
        assert 'Name or service not known' in caught.value.message


def test_no_provider_configured_still_refuses_before_any_transport(app):
    with app.app_context():
        configure(app, provider='mailgun')
        with pytest.raises(EmailNotConfiguredError):
            mailer.send_email('reader@example.com', 'Hi', 'Body.')


def test_suppression_beats_every_provider(app, monkeypatch):
    """The suppression check sits in front of the transport, so no provider
    can reach the network in a test whatever the settings say. Asserted for
    Mailgun as well as SMTP, because Mailgun is the one that would leave the
    machine over HTTP rather than refusing to connect."""
    def explode(*args, **kwargs):
        raise AssertionError('the test suite opened a connection')

    monkeypatch.setattr(mailer._mailgun_opener, 'open', explode)
    monkeypatch.setattr('smtplib.SMTP', explode)
    with app.app_context():
        for settings in (CUSTOM, GMAIL, MAILGUN):
            configure(app, **settings)
            app.config['MAIL_SUPPRESS_SEND'] = True
            mailer.send_email('reader@example.com', 'Hi', 'Body.')
            assert mailer._outbox[-1]['To'] == 'reader@example.com'
