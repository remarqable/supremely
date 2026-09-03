"""Powered-by attribution: on every public surface, in every email, and
nowhere it does not belong.

The point of the shared partial and the mailer hook is that a theme or a
kind of email nobody has written yet inherits the attribution. These tests
sweep every shipped theme and drive real sends, so adding either without it
fails here rather than shipping unattributed.
"""

from flask import g

from app.models import Content
from app.platform import mailer
from app.platform.attribution import powered_by_url
from app.platform.jobs import run_pending_jobs
from app.platform.theming import AVAILABLE_THEMES
from tests.conftest import configure_email, login_as

ACME = 'http://acme.example.test'
# Rendered into HTML, so the ampersand is escaped.
SITE_LINK = b'utm_source=powered_by&amp;utm_medium=site'


def bodies(message):
    """(text, html) of a captured message; html is '' when there is none."""
    text = message.get_body(('plain',))
    html = message.get_body(('html',))
    return (text.get_content() if text else '',
            html.get_content() if html else '')


# --- the link itself ----------------------------------------------------------

def test_the_link_carries_the_tracking_parameters():
    assert powered_by_url('site') == (
        'https://supremely.org/?utm_source=powered_by&utm_medium=site')
    assert powered_by_url('email').endswith('utm_medium=email')


def test_an_unknown_medium_falls_back_rather_than_reaching_the_url():
    """`medium` is interpolated into a URL, so the closed set is the guard."""
    assert powered_by_url('"><script>') == powered_by_url('site')
    assert powered_by_url('') == powered_by_url('site')


# --- the public site ----------------------------------------------------------

def test_every_shipped_theme_attributes(app, client, acme, globex):
    """Every built-in theme, discovered rather than listed, so a fifth one
    added later is covered without editing this test.

    Whether a theme ships its own footer (Origin, Supremely, Trailhead) or
    builds one into its layout (Midnight), the attribution is there.
    """
    builtin = [slug for slug, info in AVAILABLE_THEMES.items()
               if info['source'] == 'builtin']
    assert len(builtin) >= 4, 'the built-in themes did not load'
    for theme in builtin:
        acme.theme = theme
        acme.save()
        body = client.get('/', base_url=ACME).data
        assert b'Powered by' in body, theme
        assert b'>Supremely</a>' in body, theme
        assert SITE_LINK in body, theme


def test_a_themed_page_attributes(app, client, acme, globex):
    """Not only the front page: every surface the theme renders inherits it
    from the footer, so a new content type needs no work."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='About us', slug='about-us',
                       body='Hello', org_id=acme.id, visibility='public',
                       fields={}, tags=[])
        page.save()
        page.publish()
    assert SITE_LINK in client.get('/about-us', base_url=ACME).data


def test_the_console_does_not_attribute(app, client, acme, globex, user):
    """An operator administering Supremely does not need telling."""
    login_as(client, user)
    assert SITE_LINK not in client.get('/manage/branding', base_url=ACME).data


def test_the_community_shell_does_not_attribute(app, client, acme, globex):
    """The member area is the application, not the published site."""
    assert SITE_LINK not in client.get('/discussions/', base_url=ACME).data


# --- email --------------------------------------------------------------------

def test_every_email_attributes_including_kinds_not_written_yet(app, acme):
    """The hook is in mailer.send_email, the one place every message goes
    through, so this holds for any composer added later."""
    configure_email(app)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        mailer.send_email('someone@test', 'Subject', 'A body with no footer.')
    text, _ = bodies(mailer._outbox[-1])
    assert 'Powered by Supremely' in text
    assert 'utm_medium=email' in text


def test_a_newsletter_issue_attributes_in_both_parts(app, client, acme,
                                                     globex, user):
    """An HTML email needs it in the HTML part too, or the attribution is
    invisible in every client that prefers HTML."""
    from app.models.newsletter import Subscriber
    configure_email(app)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Subscriber.subscribe('reader@example.com', acme.id,
                             require_confirmation=False)
    mailer._outbox.clear()

    login_as(client, user)
    client.post('/manage/content/article/new', base_url=ACME, data={
        'title': 'Issue one', 'slug': 'issue-one', 'body': 'Read **this**.',
        'visibility': 'public', 'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item_id = Content.published_by_slug('article', 'issue-one').id
    client.post(f'/manage/content/{item_id}/send-newsletter', base_url=ACME,
                follow_redirects=True)
    run_pending_jobs()

    assert mailer._outbox, 'the newsletter sent nothing'
    text, html = bodies(mailer._outbox[-1])
    assert 'Powered by Supremely' in text
    assert 'utm_medium=email' in html
    assert '>Supremely</a>' in html


def test_the_operators_own_diagnostic_does_not_attribute(app, client,
                                                         platform_admin):
    """The SMTP test message is the operator checking their own plumbing,
    not published output, and its exact body is the point."""
    configure_email(app)
    login_as(client, platform_admin)
    client.post('/admin/settings/test-email', data={'to': 'ops@example.test'})
    assert mailer._outbox, 'no test email was sent'
    text, _ = bodies(mailer._outbox[-1])
    assert 'Powered by' not in text


def test_a_transactional_email_attributes(app, client, acme, globex, user):
    """The subscription confirmation: composed nowhere near the newsletter
    issue, and plain text only."""
    configure_email(app)
    client.post('/subscribe', base_url=ACME,
                data={'email': 'newcomer@example.com'})
    run_pending_jobs()

    assert mailer._outbox, 'no confirmation email was sent'
    text, _ = bodies(mailer._outbox[-1])
    assert 'Powered by Supremely' in text
    assert 'utm_medium=email' in text
