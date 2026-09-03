"""Installation-level email delivery.

Email is optional infrastructure: Supremely must install, authenticate,
publish, and onboard users with no email service configured. Everything here
degrades gracefully when SMTP is absent.
"""

import smtplib
from email.message import EmailMessage

from flask import current_app

from app.platform.errors import EmailNotConfiguredError
from app.platform.logger import get_logger

log = get_logger()

SMTP_KEYS = ('email.smtp_host', 'email.smtp_port', 'email.smtp_username',
             'email.smtp_password', 'email.from_address', 'email.use_tls')


def email_settings() -> dict:
    from app.models import InstallationSetting
    return InstallationSetting.get_map('email.')


def is_email_configured() -> bool:
    settings = email_settings()
    return bool(settings.get('email.smtp_host') and settings.get('email.from_address'))


def send_email(to: str, subject: str, body: str, html: str | None = None,
               attribution: bool = True) -> None:
    """Send one email. Raises EmailNotConfiguredError when no SMTP exists.

    `attribution` appends the powered-by footer, which is why it defaults to
    on: a kind of email nobody has written yet inherits it here rather than
    being remembered individually. Pass False for a message that is not
    published output -- the operator's own SMTP diagnostic, say, where an
    exact body is the point.
    """
    from app.platform.attribution import email_html, email_text
    settings = email_settings()
    if not (settings.get('email.smtp_host') and settings.get('email.from_address')):
        raise EmailNotConfiguredError()

    msg = EmailMessage()
    msg['From'] = settings['email.from_address']
    msg['To'] = to
    msg['Subject'] = subject
    # Appended here rather than in each composer, so a kind of email nobody
    # has written yet still carries it.
    msg.set_content(body + email_text() if attribution else body)
    if html:
        msg.add_alternative(html + email_html() if attribution else html,
                            subtype='html')

    if current_app.config.get('MAIL_SUPPRESS_SEND'):
        log.info('email_suppressed', to=to, subject=subject)
        _outbox.append(msg)
        return

    host = settings['email.smtp_host']
    port = int(settings.get('email.smtp_port') or 587)
    use_tls = settings.get('email.use_tls', 'true').strip().lower() != 'false'

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        username = settings.get('email.smtp_username')
        if username:
            smtp.login(username, settings.get('email.smtp_password', ''))
        smtp.send_message(msg)
    log.info('email_sent', to=to, subject=subject)


def try_send_email(to: str, subject: str, body: str, html: str | None = None,
                   attribution: bool = True) -> bool:
    """Best-effort send: False (never an error) when email is not configured."""
    try:
        send_email(to, subject, body, html=html, attribution=attribution)
        return True
    except EmailNotConfiguredError:
        return False


# Captured messages when MAIL_SUPPRESS_SEND is on (tests).
_outbox: list[EmailMessage] = []
