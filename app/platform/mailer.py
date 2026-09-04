"""Installation-level email delivery.

Email is optional infrastructure: Supremely must install, authenticate,
publish, and onboard users with no email service configured. Everything here
degrades gracefully when SMTP is absent.

One seam, several providers. Everything that sends calls send_email and
knows nothing about which provider is configured; the provider is chosen
here, once, from the installation settings.
"""

import base64
import json
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from email.message import EmailMessage

from flask import current_app

from app.platform.errors import (
    AppError,
    EmailNotConfiguredError,
    EmailSendError,
    ValidationError,
)
from app.platform.logger import get_logger

log = get_logger()

# Named providers. Every one of them is reachable through 'custom' by hand;
# naming them is about not making an operator find the host name, the port
# and the fact that Gmail wants an app password rather than a password.
#
# 'transport' is how the message actually leaves: 'smtp' speaks to a relay,
# 'mailgun' posts to Mailgun's HTTP API. A preset fills in what the operator
# would otherwise have to type, and those values are not stored -- they are
# read back from here at send time, so a provider whose host changes is a
# change here rather than a migration of everybody's settings.
EMAIL_PROVIDERS: dict[str, dict[str, object]] = {
    'custom': {
        'transport': 'smtp',
        'fields': ('smtp_host', 'smtp_port', 'smtp_username',
                   'smtp_password', 'use_tls'),
    },
    'gmail': {
        'transport': 'smtp',
        'preset': {'host': 'smtp.gmail.com', 'port': '587', 'use_tls': 'true'},
        'fields': ('smtp_username', 'smtp_password'),
    },
    'mailgun': {
        'transport': 'mailgun',
        'fields': ('mailgun_api_key', 'mailgun_domain', 'mailgun_region'),
    },
}
DEFAULT_PROVIDER = 'custom'

# Never echoed back into the form, so a blank one on save means "keep what
# is stored" rather than "clear it".
EMAIL_SECRET_FIELDS: tuple[str, ...] = ('smtp_password', 'mailgun_api_key')

# Mailgun's two regions are two hosts. Anything else is not a region.
MAILGUN_REGIONS: dict[str, str] = {'us': 'https://api.mailgun.net',
                   'eu': 'https://api.eu.mailgun.net'}

# The sending domain lands in a URL path, so it is checked rather than
# trusted: a value shaped like a path or a host of its own would aim the
# request somewhere Mailgun is not.
MAILGUN_DOMAIN_RE = re.compile(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?'
                               r'(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+')


def email_settings() -> dict:
    from app.models import InstallationSetting
    return InstallationSetting.get_map('email.')


def email_provider(settings: dict | None = None) -> str:
    """Which named provider is configured. Never an unknown one."""
    settings = email_settings() if settings is None else settings
    provider = (settings.get('email.provider') or '').strip()
    return provider if provider in EMAIL_PROVIDERS else DEFAULT_PROVIDER


def smtp_target(settings: dict) -> tuple[str, int, bool]:
    """(host, port, use_tls) for the SMTP transport.

    A preset wins over the stored values: the form does not ask for a host
    it already knows, so there is nothing stored to prefer.
    """
    preset = EMAIL_PROVIDERS[email_provider(settings)].get('preset', {})
    host = preset.get('host') or settings.get('email.smtp_host', '')
    port = smtp_port(preset.get('port') or settings.get('email.smtp_port'))
    use_tls = (preset.get('use_tls')
               or settings.get('email.use_tls', 'true'))
    return host, port, str(use_tls).strip().lower() != 'false'


def is_email_configured() -> bool:
    """Enough settings for a send to be worth attempting.

    Asked all over the application to decide whether to offer email at all,
    so it answers per provider: Gmail needs no host, Mailgun needs no SMTP
    anything, and a half-filled form is not configured.

    "Configured" has to mean sendable, not merely non-empty. A row can be
    written by something other than the form, and answering True for
    settings that raise at send time turns a misconfiguration into a job
    that fails over and over.
    """
    settings = email_settings()
    try:
        from_address(settings.get('email.from_address', ''))
    except ValidationError:
        return False
    provider = email_provider(settings)
    if provider == 'mailgun':
        if not settings.get('email.mailgun_api_key'):
            return False
        try:
            mailgun_domain(settings.get('email.mailgun_domain', ''))
        except ValidationError:
            return False
        return True
    try:
        smtp_target(settings)
    except ValidationError:             # a port that is not a port
        return False
    if provider == 'gmail':
        return bool(settings.get('email.smtp_username')
                    and settings.get('email.smtp_password'))
    return bool(settings.get('email.smtp_host'))


def _build_message(sender: str, to: str, subject: str, text: str,
                   html: str | None) -> EmailMessage:
    msg = EmailMessage()
    try:
        msg['From'] = sender
        msg['To'] = to
        msg['Subject'] = subject
    except ValueError as e:
        # A newline in an address or subject is header injection, and the
        # email package refuses it. Refused as a send failure rather than
        # as a stray ValueError, so callers see one kind of error.
        raise EmailSendError(str(e)) from e
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    return msg


def send_email(to: str, subject: str, body: str, html: str | None = None,
               attribution: bool = True) -> None:
    """Send one email. Raises EmailNotConfiguredError when no provider exists.

    `attribution` appends the powered-by footer, which is why it defaults to
    on: a kind of email nobody has written yet inherits it here rather than
    being remembered individually. Pass False for a message that is not
    published output -- the operator's own delivery diagnostic, say, where
    an exact body is the point.
    """
    from app.platform.attribution import email_html, email_text
    settings = email_settings()
    if not is_email_configured():
        raise EmailNotConfiguredError()

    # Appended here rather than in each composer, so a kind of email nobody
    # has written yet still carries it.
    text = body + email_text() if attribution else body
    if html:
        html = html + email_html() if attribution else html
    sender = settings['email.from_address']

    # Before the provider is chosen, so no transport can reach the network
    # in a test whatever the settings say.
    if current_app.config.get('MAIL_SUPPRESS_SEND'):
        log.info('email_suppressed', to=to, subject=subject)
        _outbox.append(_build_message(sender, to, subject, text, html))
        return

    provider = email_provider(settings)
    if EMAIL_PROVIDERS[provider]['transport'] == 'mailgun':
        _send_mailgun(settings, sender, to, subject, text, html)
    else:
        _send_smtp(settings, _build_message(sender, to, subject, text, html))
    log.info('email_sent', to=to, subject=subject, provider=provider)


def _send_smtp(settings: dict, msg: EmailMessage) -> None:
    host, port, use_tls = smtp_target(settings)
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            username = settings.get('email.smtp_username')
            if username:
                smtp.login(username, settings.get('email.smtp_password', ''))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        # The same shape Mailgun failures take, so everything that sends has
        # one kind of failure to handle rather than one per transport.
        raise EmailSendError(str(e)) from e


def mailgun_domain(value: str) -> str:
    """A sending domain, or a refusal.

    The value lands in a URL path, so it is checked rather than trusted: a
    value shaped like a path or a host of its own would aim the request
    somewhere Mailgun is not. Checked when it is saved so the operator hears
    about it then, and again here, because a row can be written by something
    other than the form.
    """
    from app.platform.i18n import t
    domain = (value or '').strip().lower()
    if not MAILGUN_DOMAIN_RE.fullmatch(domain):
        raise ValidationError(t('admin.mailgun_domain_invalid',
                                domain=domain or '(empty)'))
    return domain


def mailgun_endpoint(settings: dict) -> str:
    """The messages URL for the configured domain and region."""
    domain = mailgun_domain(settings.get('email.mailgun_domain', ''))
    region = (settings.get('email.mailgun_region') or 'us').strip().lower()
    base = MAILGUN_REGIONS.get(region, MAILGUN_REGIONS['us'])
    return f'{base}/v3/{urllib.parse.quote(domain)}/messages'


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib follows redirects and copies the headers along with them, so a
    3xx from Mailgun's host would replay the API key at whatever it named.
    There is no redirect worth following here."""

    def redirect_request(self, req, fp, code: int, msg: str, headers,
                         newurl: str) -> None:
        return None


_mailgun_opener = urllib.request.build_opener(_NoRedirects)


def _send_mailgun(settings: dict, sender: str, to: str, subject: str,
                  text: str, html: str | None) -> None:
    fields = {'from': sender, 'to': to, 'subject': subject, 'text': text}
    if html:
        fields['html'] = html
    request = urllib.request.Request(
        mailgun_endpoint(settings),
        data=urllib.parse.urlencode(fields).encode())
    token = base64.b64encode(
        f'api:{settings.get("email.mailgun_api_key", "")}'.encode()).decode()
    request.add_header('Authorization', f'Basic {token}')
    try:
        with _mailgun_opener.open(request, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as e:
        raise EmailSendError(_mailgun_message(e)) from e
    except OSError as e:            # DNS, TLS, connection refused, timeout
        raise EmailSendError(str(e)) from e


def _mailgun_message(error: urllib.error.HTTPError) -> str:
    """Mailgun's own words, so a wrong key says so instead of "502"."""
    try:
        detail = json.loads(error.read().decode())['message']
    except Exception:               # noqa: BLE001 -- any unreadable body
        detail = error.reason
    return f'Mailgun refused the message ({error.code}): {detail}'


def apply_settings(provider: str, form: Mapping[str, str]) -> None:
    """Store one provider's settings from a submitted form.

    Everything is checked before anything is written, because each setting
    commits as it is set: a refusal half way would leave the installation
    pointed at a provider it cannot send through. Only the chosen provider's
    own fields are written, so setting up Mailgun does not disturb an SMTP
    host somebody typed.
    """
    from app.models import InstallationSetting
    from app.platform.i18n import t
    if provider not in EMAIL_PROVIDERS:
        raise ValidationError(t('admin.email_provider_unknown'))
    fields = EMAIL_PROVIDERS[provider]['fields']
    if 'mailgun_domain' in fields:
        mailgun_domain(form.get('mailgun_domain', ''))
    if 'smtp_port' in fields:
        smtp_port(form.get('smtp_port', ''))

    sender = from_address(form.get('from_address', ''))

    InstallationSetting.set('email.provider', provider)
    InstallationSetting.set('email.from_address', sender)
    for field in fields:
        if field == 'use_tls':
            InstallationSetting.set(
                'email.use_tls',
                'true' if form.get('use_tls') == 'on' else 'false')
            continue
        value = (form.get(field) or '').strip()
        # A blank secret means the admin did not retype it: the stored one is
        # never echoed back into the form, so there is nothing to retype from.
        if field in EMAIL_SECRET_FIELDS and not value:
            continue
        InstallationSetting.set(f'email.{field}', value)


def from_address(value: str) -> str:
    """The address messages will come from, or a refusal.

    Checked because is_email_configured treats a non-empty one as enough:
    an address that is not an address would read as configured and fail on
    every send, and a newline in it is header injection.
    """
    from app.models.newsletter import EMAIL_RE
    from app.platform.i18n import t
    address = (value or '').strip()
    if not EMAIL_RE.match(address):
        raise ValidationError(t('admin.from_address_invalid',
                                address=address or '(empty)'))
    return address


def smtp_port(value: str) -> int:
    """A port, or a refusal. Checked when it is saved, so a typo is caught
    there rather than by the first newsletter that will not go out."""
    from app.platform.i18n import t
    text = (value or '').strip() or '587'
    try:
        port = int(text)
    except ValueError:
        raise ValidationError(t('admin.smtp_port_invalid', port=text)) from None
    if not 1 <= port <= 65535:
        raise ValidationError(t('admin.smtp_port_invalid', port=text))
    return port


def try_send_email(to: str, subject: str, body: str, html: str | None = None,
                   attribution: bool = True) -> bool:
    """Best-effort send: False rather than an error.

    Covers a provider that refuses and settings that cannot send, not only
    the absent-provider case: callers use this exactly where email is a
    courtesy and its failure must not fail the thing that asked for it.
    """
    try:
        send_email(to, subject, body, html=html, attribution=attribution)
        return True
    except EmailNotConfiguredError:
        return False
    except AppError as e:
        log.warning('email_send_failed', to=to, error=e.message)
        return False


# Captured messages when MAIL_SUPPRESS_SEND is on (tests).
_outbox: list[EmailMessage] = []
