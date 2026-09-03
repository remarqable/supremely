"""Powered-by attribution.

One home for the link every Supremely-generated public surface carries, so
a new theme, a new content type or a new kind of email inherits it instead
of each one growing its own copy. Two consumers:

    web    partials/_powered_by.html, included by every theme footer
    email  appended to every message by mailer.send_email

The link is tagged so traffic it sends is identifiable in the project's own
analytics, and `medium` says which kind of surface sent it.
"""

SITE_URL = 'https://supremely.org/'
PRODUCT = 'Supremely'

# What `medium` may be. A closed set on purpose: the value is interpolated
# into a URL, and this is the only thing standing between a future caller
# passing something unvalidated and a broken (or crafted) link.
MEDIUMS = ('site', 'email')


def powered_by_url(medium: str = 'site') -> str:
    """The attribution link, tagged with where the visit came from."""
    if medium not in MEDIUMS:
        medium = 'site'
    return f'{SITE_URL}?utm_source=powered_by&utm_medium={medium}'


def email_text() -> str:
    """The plain-text footer appended to every outgoing email.

    A blank line and nothing else. Not "-- ", the RFC 3676 signature
    marker, because clients that honour it collapse everything below --
    which would hide the attribution in exactly the clients that read the
    standard. And no bare "--" either: the newsletter already ends with one
    of its own before the unsubscribe line, and two of them read like two
    signatures.
    """
    from app.platform.i18n import t
    # Keyed like every other user-visible string. t() falls back to the
    # default language outside a request, which is where email is composed.
    line = t('site.powered_by', product=PRODUCT)
    return f'\n{line}\n{powered_by_url("email")}\n'


def email_html() -> str:
    """The HTML footer appended to every outgoing HTML email.

    Inline styles and nothing else: an email client has no stylesheet of
    ours and many strip <style> blocks entirely.
    """
    from markupsafe import Markup

    from app.platform.i18n import t
    link = Markup('<a href="{url}" style="color:#888">{product}</a>').format(
        url=powered_by_url('email'), product=PRODUCT)
    # The whole sentence comes from the catalog, so a translation is free to
    # put the product name wherever its grammar wants it.
    line = t('site.powered_by', product=link)
    return (
        '<p style="font-family:sans-serif;font-size:12px;color:#888;'
        f'margin-top:24px">{line}</p>'
    )
