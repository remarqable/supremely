"""The email layout, and the messages that use it.

Email is the one surface nobody sees while building it: it renders in a
job, it goes to somebody else, and a mail client is not a browser. So the
things that go wrong quietly are what is asserted here.
"""

import re

import pytest
from flask import g

from app.platform.emails import invitation_message, render_email, render_message
from tests.conftest import configure_email

ACME = 'http://acme.example.test'


def rendered(app, org, **fields):
    with app.app_context():
        from app.platform.tenant import org_scope
        with org_scope(org.id):
            return render_email('emails/invitation.html', org=org,
                                subject='s', **fields)


# --- what a mail client can actually use ---------------------------------------

def test_every_link_is_a_whole_address(app, acme):
    """A mail client has no origin to resolve a relative href against, so a
    button pointing at /invite/abc is a dead button. The logo has the same
    problem and arrives as a broken image."""
    html = rendered(app, acme, heading='h', intro='i',
                    action='Accept', action_url='/invite/abc')
    for href in re.findall(r'href="([^"]+)"', html):
        assert href.startswith(('http://', 'https://', 'mailto:')), href
    for src in re.findall(r'src="([^"]+)"', html):
        assert src.startswith(('http://', 'https://')), src


def test_the_address_is_written_out_as_well_as_linked(app, acme):
    """A button is unclickable in a text-only client and unreadable in a
    printout, so the address it points at is on the page too."""
    html = rendered(app, acme, heading='h', intro='i',
                    action='Accept', action_url='/invite/abc')
    assert html.count('/invite/abc') >= 2


def test_the_layout_carries_no_stylesheet(app, acme):
    """Mail clients strip style blocks and know nothing about flexbox or
    grid, so everything here is inline and laid out in tables."""
    html = rendered(app, acme, heading='h', intro='i')
    assert '<style' not in html
    assert 'class=' not in html
    assert 'display:flex' not in html and 'display:grid' not in html


def test_a_message_says_which_language_it_is_in(app, acme, monkeypatch):
    """Rendered through the Jinja environment, so none of the context
    processors run: without passing these the document claims English and
    left-to-right whatever the reader's language.

    Asserted against a language that is not the default, because matching
    the shape of the tag passes just as well when both values are nailed to
    'en' and 'ltr' -- which is the bug.
    """
    # Patched on i18n, because emails.py imports these inside the function
    # and so resolves them at call time.
    import app.platform.i18n as i18n_module
    monkeypatch.setattr(i18n_module, 'get_lang', lambda: 'ar')
    monkeypatch.setattr(i18n_module, 'is_rtl', lambda: True)
    html = rendered(app, acme, heading='h', intro='i')
    assert '<html lang="ar" dir="rtl">' in html
    # ...and a quote bar follows the direction the document declares.
    quoted = render_email('emails/notification.html', subject='s', org=acme,
                          heading='h', snippet='q', kind='k')
    assert 'border-right' in quoted and 'border-left' not in quoted


# --- the attribution, which used to arrive twice --------------------------------

def test_the_attribution_appears_once_and_inside_the_document(app, acme):
    """The mailer appends its own copy unless it is told not to. With the
    layout carrying one too, a reader got it twice, the second time after
    the closing html tag."""
    text, html = render_message(
        'emails/invitation.html', subject='s', org=acme, text='body',
        heading='h', intro='i')
    assert html.count('Powered by') == 1
    assert text.count('Powered by') == 1
    assert html.rstrip().endswith('</html>')
    # Not a paragraph inside a paragraph: the attribution is already one.
    assert '<p><p' not in html.replace(' ', '')


def test_every_message_carries_the_attribution_once_in_both_parts(
        app, acme, user, monkeypatch):
    """Sent, not grepped.

    An earlier version of this read the senders' source looking for
    `attribution=False`, which passes whether or not a message is ever sent
    and cannot see a half that lost its attribution altogether. That is
    exactly what happened: the fix for a doubled footer deleted the
    newsletter's plain-text one, and the source still read correctly.
    """
    from app.models import Content
    from app.models.newsletter import Subscriber
    from app.platform import newsletter
    from app.platform.tenant import org_scope

    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Issue one', slug='issue-one',
                       body='Read this.', org_id=acme.id, fields={}, tags=[],
                       visibility='public')
        item.save()
        item.publish()
        who = Subscriber.subscribe('reader@example.com', acme.id,
                                   require_confirmation=False)

    sent = []
    with app.app_context(), org_scope(acme.id):
        _, text, html = newsletter.compose_email(item, acme, who)
        sent.append(('newsletter', text, html))
        _, itext, ihtml = invitation_message(acme, f'{ACME}/invite/abc')
        sent.append(('invitation', itext, ihtml))

    for name, text, html in sent:
        assert text.count('Powered by') == 1, f'{name} text'
        assert html.count('Powered by') == 1, f'{name} html'
        assert html.rstrip().endswith('</html>'), name


def test_the_newsletter_says_what_it_is_and_how_to_stop_it(app, acme):
    """The only message with a body of its own, and the only one that has to
    offer a way out of it."""
    from app.models import Content
    from app.models.newsletter import Subscriber
    from app.platform import newsletter
    from app.platform.tenant import org_scope

    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Issue one', slug='issue-one',
                       body='Read **this**.', org_id=acme.id, fields={},
                       tags=[], visibility='public')
        item.save()
        item.publish()
        who = Subscriber.subscribe('reader@example.com', acme.id,
                                   require_confirmation=False)
        token = who.token

    with app.app_context(), org_scope(acme.id):
        subject, text, html = newsletter.compose_email(item, acme, who)

    assert subject == 'Issue one'
    assert '<strong>this</strong>' in html          # the body, rendered
    assert f'/unsubscribe/{token}' in html
    assert f'/unsubscribe/{token}' in text
    assert '/blog/issue-one' in html


# --- escaping -------------------------------------------------------------------

def test_a_name_somebody_chose_cannot_carry_markup(app, acme):
    """Every value in these templates is somebody's typing: a display name,
    a post title, an organization's own name."""
    html = rendered(app, acme,
                    heading='<script>alert(1)</script>',
                    intro='"><img src=x onerror=alert(1)>',
                    action='<b>go</b>', action_url='/x')
    # Asserted on tags, not on the words inside them: escaped text still
    # reads "onerror=", and a test looking for that would pass on markup
    # that was never escaped at all.
    assert '<script>' not in html
    assert '<img' not in html
    assert '<b>go</b>' not in html
    # ...and the escaped form is there, which is what proves it was escaped
    # rather than dropped.
    assert '&lt;script&gt;' in html

    # Every template, not just the one: the newsletter renders a heading and
    # a body of its own, and only the body is sanitised upstream.
    from app.platform.tenant import org_scope
    with app.app_context(), org_scope(acme.id):
        issue = render_email('emails/newsletter.html', subject='s', org=acme,
                             heading='<script>alert(1)</script>',
                             body_html='<p>fine</p>', field_table='',
                             action='a', action_url='/x',
                             footer_note='n', unsubscribe_url='/u',
                             unsubscribe_label='stop')
        note = render_email('emails/notification.html', subject='s', org=acme,
                            kind='<b>k</b>', heading='<i>h</i>',
                            snippet='<script>alert(2)</script>')
    for page in (issue, note):
        assert '<script>' not in page
        assert '<b>k</b>' not in page and '<i>h</i>' not in page


# --- rendering where there is no request ----------------------------------------

def test_a_message_renders_in_a_job(app, acme):
    """A newsletter is a job. It knows its tenant through org_scope and has
    no request, so anything reaching for g or a context processor would
    fail here rather than in a test."""
    from flask import has_request_context

    from app.platform.tenant import org_scope
    with app.app_context(), org_scope(acme.id):
        assert not has_request_context()
        html = render_email('emails/invitation.html', subject='s', org=acme,
                            heading='h', intro='i', action='a',
                            action_url='/x')
    assert acme.site_name in html
    assert 'http' in html


def test_a_message_belongs_to_one_organization(app, acme, globex):
    """A delivery renders many messages and the tenant is already known, so
    the organization is passed rather than read from the surroundings: two
    organizations must never borrow each other's name."""
    from app.platform.tenant import org_scope
    with app.app_context(), org_scope(acme.id):
        for_globex = render_email('emails/invitation.html', subject='s',
                                  org=globex, heading='h', intro='i')
    assert globex.site_name in for_globex
    assert acme.site_name not in for_globex


def test_a_message_without_an_organization_is_refused(app):
    """Every email speaks for one. Rendering with none in force would put
    somebody else's name on it, or no name at all."""
    with app.app_context(), pytest.raises(ValueError):
        render_email('emails/invitation.html', subject='s',
                     heading='h', intro='i')


# --- the branding it carries ----------------------------------------------------

def test_the_organizations_colour_is_on_the_action(app, acme):
    """One look for every installation, and what changes is the
    organization's own colour, name and logo."""
    acme.brand_primary = '#aa1166'
    acme.save()
    html = rendered(app, acme, heading='h', intro='i', action='a',
                    action_url='/x')
    assert '#aa1166' in html


def test_a_logo_is_used_when_there_is_one_and_the_name_when_there_is_not(
        app, acme):
    from app.models import Upload
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert acme.site_name in rendered(app, acme, heading='h')  # no logo

        upload = Upload(filename='l.png', key='l.png', content_type='image/png',
                        size=10, org_id=acme.id, visibility='public')
        upload.save()
        acme.update_settings(logo_upload_id=upload.id)
        upload_id = upload.id
    html = rendered(app, acme, heading='h')
    # An upload is served by id, not by the name it was uploaded under.
    assert '<img' in html
    assert f'/files/{upload_id}/' in html
    assert f'alt="{acme.site_name}"' in html


def test_the_logo_is_resolved_once_for_a_whole_delivery(app, acme):
    """A delivery is two hundred messages, and it commits after each one.

    Hoisting the row did not help: the commit expires it, so every attribute
    the template read went back to the database anyway. What is hoisted is
    the finished address, which survives a commit because it is a string.
    """
    import sqlalchemy as sa

    from app.extensions import db
    from app.models import Upload
    from app.platform.emails import logo_src
    from app.platform.tenant import org_scope

    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload = Upload(filename='l.png', key='l.png',
                        content_type='image/png', size=10, org_id=acme.id,
                        visibility='public')
        upload.save()
        acme.update_settings(logo_upload_id=upload.id)

    seen = {'n': 0}

    @sa.event.listens_for(sa.engine.Engine, 'before_cursor_execute')
    def before(conn, cursor, statement, params, context, many):
        if 'FROM upload' in statement:
            seen['n'] += 1

    try:
        with app.app_context(), org_scope(acme.id):
            src = logo_src(acme)
            assert src and src.startswith('http')
            seen['n'] = 0
            for _ in range(5):
                render_email('emails/invitation.html', subject='s', org=acme,
                             logo_src=src, heading='h')
                db.session.commit()      # what the delivery loop does
            hoisted = seen['n']

            seen['n'] = 0
            for _ in range(5):
                render_email('emails/invitation.html', subject='s', org=acme,
                             heading='h')
                db.session.commit()
            each_time = seen['n']
    finally:
        sa.event.remove(sa.engine.Engine, 'before_cursor_execute', before)

    assert hoisted == 0, hoisted
    assert each_time >= 5, each_time     # one lookup per message without it


# --- the messages themselves ----------------------------------------------------

def test_an_invitation_says_who_it_is_from_and_what_to_do(app, acme):
    subject, text, html = invitation_message(acme, f'{ACME}/invite/abc')
    assert acme.name in subject
    assert f'{ACME}/invite/abc' in text and f'{ACME}/invite/abc' in html
    assert acme.site_name in html


@pytest.mark.parametrize('kind_wanted', ['reply.followed', 'reply.to_author',
                                         'mention', 'moderation'])
def test_a_notification_says_what_kind_of_notice_it_is(
        app, acme, user, monkeypatch, kind_wanted):
    """A mention, a reply and a moderation notice arrived looking identical
    when the heading was generic and the eyebrow repeated the post title."""
    from app.models.notification import Notification
    from app.platform import notify
    from app.platform.i18n import t
    from app.platform.tenant import org_scope

    sent = {}
    with app.test_request_context(base_url=ACME):
        g.org = acme
        note = Notification(user_id=user.id, org_id=acme.id,
                            type=kind_wanted,
                            payload={'title': 'A post', 'actor_name': 'Dana',
                                     'snippet': 'hello', 'url': '/x'})
        note.save()
        note_id = note.id

    # Through the real mailer, and read out of its outbox. Replacing
    # send_email intercepts the message before the mailer would append its
    # own attribution, so a doubled footer is invisible to a test that
    # stands in front of it.
    from app.platform import mailer
    configure_email(app)
    mailer._outbox.clear()
    with app.app_context(), org_scope(acme.id):
        notify.deliver_notification_email({'notification_id': note_id})
    assert mailer._outbox, 'no email was sent'
    text, html = bodies(mailer._outbox[-1])
    sent.update(body=text, html=html)

    assert sent, 'no email was composed'
    # The type is dotted and the catalogue key is not, so a missing
    # transform blanks the label for exactly the two commonest kinds.
    key = f"notifications.type_{kind_wanted.replace('.', '_')}"
    label = t(key)
    assert label in sent['html'], kind_wanted

    # Each kind reads as its own thing. Asserted against the eyebrow rather
    # than against a phrase no longer in the catalogue: collapsing the
    # heading back onto the label leaves both halves saying the same words,
    # which is the defect, and a test looking for a deleted string cannot
    # see it.
    heading = t(f'{key}_email', actor='Dana')
    assert heading != f'{key}_email', f'no heading for {kind_wanted}'
    assert heading in sent['html'], kind_wanted
    assert heading != label, kind_wanted

    # The text half is what a screen reader and a text-only client read. It
    # was still printing the machine name of the type and a relative link
    # while the markup beside it was rebuilt.
    # The shape the old body had: the actor, a dash, and the machine name
    # of the type. Matched on that rather than on the bare word, because
    # "mention" is also a substring of the sentence that replaced it.
    assert f'- {kind_wanted}' not in sent['body']
    assert heading in sent['body'], kind_wanted
    assert not sent['body'].lstrip().startswith('-')
    for line in sent['body'].splitlines():
        if line.startswith('/'):
            raise AssertionError(f'relative link in the text part: {line}')

    assert sent['html'].count('Powered by') == 1
    assert sent['body'].count('Powered by') == 1


def bodies(message):
    """(text, html) of a captured message; html is '' when there is none."""
    text = message.get_body(('plain',))
    html = message.get_body(('html',))
    return (text.get_content() if text else '',
            html.get_content() if html else '')


def test_an_address_that_is_already_whole_is_left_alone(app, acme):
    """The guard that makes one helper serve both callers.

    Without it an already-absolute action link is prefixed a second time and
    the newsletter's own button points at http://host/http://host/blog/...,
    which is what the two helpers used to disagree about.
    """
    from app.platform.emails import absolute_url_for
    from app.platform.tenant import org_scope, org_url

    with app.app_context(), org_scope(acme.id):
        absolute = absolute_url_for(acme)
        home = org_url(acme, '/')
        assert absolute('/blog/x') == org_url(acme, '/blog/x')
        whole = org_url(acme, '/blog/x')
        assert absolute(whole) == whole                  # not prefixed twice
        assert absolute('https://elsewhere.test/x') == 'https://elsewhere.test/x'
        assert absolute('') == ''
        # A protocol-relative address is somebody else's host wearing no
        # scheme; it becomes a path on this organization's own.
        assert absolute('//evil.test/x').startswith(home.rstrip('/'))
