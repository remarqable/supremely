"""Rendering an email.

One layout for every message (app/views/emails/base.html), filled in by a
small template per message. A theme never reaches into email: the look is
the community shell's, and what changes between installations is the
organization's own logo, name and colour.

Rendered through the Jinja environment rather than flask.render_template,
which is not a detail. render_template runs the application's context
processors, and those read the request -- so a newsletter, which is a job
with no request, could not render an email at all.
"""

from typing import TYPE_CHECKING

from flask import current_app
from markupsafe import Markup

if TYPE_CHECKING:
    from app.models.organization import Organization


def render_email(template: str, *, subject: str, org=None, **fields) -> str:
    """One message as HTML, ready to send.

    `org` is the organization the message speaks for. Passed in rather than
    read off `g`, because a job knows its tenant through org_scope and has
    no request to read: taking it from the caller is the only way the same
    function serves a controller and a worker.
    """
    from app.platform.tenant import current_org
    org = org if org is not None else current_org()
    if org is None:
        raise ValueError('An email speaks for an organization; none is in force')
    from app.platform.i18n import get_lang, is_rtl
    # Passed in, not read from a context processor: this renders through the
    # Jinja environment so none of them run, and a message composed in a job
    # would otherwise always claim to be English and left to right.
    # A finished address rather than the row it came from: a delivery
    # commits after each recipient, which expires the instance, so a hoisted
    # Upload went back to the database for every attribute the template read
    # and saved nothing at all. A string survives.
    if 'logo_src' not in fields:
        fields['logo_src'] = logo_src(org)
    return current_app.jinja_env.get_template(template).render(
        subject=subject, org=org, attribution=fields.pop('attribution', None),
        absolute_url=absolute_url_for(org),
        lang=get_lang(), is_rtl=is_rtl(), **fields)


def render_message(template: str, *, subject: str, text: str, org=None,
                   attribution: bool = True, **fields) -> tuple[str, str]:
    """The two halves of one message: the text part and the HTML part.

    Both are sent, always. The text is the fallback a text-only client and a
    screen reader read, so it is written first and the markup wraps it
    rather than replacing it.

    The attribution is put inside both halves here, which is why every
    caller passes attribution=False to send_email: the mailer appends its
    own copy otherwise, and a reader gets it twice, the second time after
    the closing html tag.
    """
    from app.platform.attribution import email_html, email_text
    html = render_email(
        template, subject=subject, org=org,
        attribution=Markup(email_html()) if attribution else None, **fields)
    return (f'{text}\n{email_text()}' if attribution else text), html


def logo_src(org) -> str | None:
    """The organization's logo as a whole address, or nothing.

    Resolved to a string here so a caller sending in bulk can work it out
    once and hand the same value to every message.
    """
    logo = org.logo()
    if logo is None:
        return None
    variant = 'medium' if logo.has_variants else 'original'
    return absolute_url_for(org)(logo.url(variant))


def absolute_url_for(org):
    """A path made whole against one organization's own address.

    An email carries no origin to resolve a relative href or src against, so
    a logo and an action link both need the entire address or they arrive
    broken. Bound to an organization rather than reading the ambient one,
    because a delivery renders many messages and the tenant is already known.
    """
    from app.platform.tenant import org_url

    def absolute(path: str) -> str:
        return org_url(org, path) if path and path.startswith('/') else path

    return absolute


def password_reset_message(org: 'Organization',
                           reset_url: str) -> tuple[str, str, str]:
    """(subject, text, html) telling somebody how to get back in.

    Sent only where the installation has email; the link works the same
    whether it arrives this way or is read out over the phone, because
    nothing about it depends on the message. Says who it is from and that
    it can be ignored, since a reset nobody asked for is the shape an
    attack takes and the account holder is the one who can tell.
    """
    from app.models.password_reset import PasswordReset
    from app.platform.i18n import t
    subject = t('members.reset_email_subject', org=org.site_name)
    text, html = render_message(
        'emails/password_reset.html', subject=subject, org=org,
        text=t('members.reset_email_body', org=org.site_name, url=reset_url),
        heading=t('members.reset_email_heading'),
        intro=t('members.reset_email_intro', org=org.site_name),
        action=t('members.reset_email_action'), action_url=reset_url,
        outro=t('members.reset_email_outro',
                hours=PasswordReset.EXPIRY_HOURS))
    return subject, text, html


def invitation_message(org, invite_url: str) -> tuple[str, str, str]:
    """(subject, text, html) inviting somebody to join an organization.

    Here rather than in the route that sends it, beside the newsletter's own
    composer: a controller decides that an invitation should go out, and
    what it says is not its business.
    """
    from app.platform.i18n import t
    subject = t('members.invite_email_subject', org=org.name)
    text, html = render_message(
        'emails/invitation.html', subject=subject, org=org,
        text=t('members.invite_email_body', org=org.name, url=invite_url),
        heading=t('members.invite_email_heading', org=org.site_name),
        intro=t('members.invite_email_intro', org=org.site_name),
        action=t('members.invite_email_action'), action_url=invite_url,
        outro=t('members.invite_email_expiry'))
    return subject, text, html
