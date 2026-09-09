"""Preview keeps you on the page you are previewing.

What each theme does is checked in tests/test_platform/test_theme_contract.py,
which is where a theme's obligations belong. This file is about what the
lock does and does not cover.

A preview used to render the site's own header and footer fully live, so a
nav link, the logo or the subscribe button would carry you off the draft you
had opened, and nothing said it would. The chrome is now inert while a
preview is on screen, and the banner is the one thing that still goes
anywhere.
"""

from flask import g

from app.extensions import db
from app.models import Content
from app.platform.theming import AVAILABLE_THEMES
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def publish(app, org, author_id, type='article', slug='field-notes',
            title='Field Notes'):
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type=type, title=title, slug=slug, body='Words.',
                       org_id=org.id, visibility='public', fields={}, tags=[],
                       created_by_id=author_id)
        item.save()
        item.publish()
        content_id = item.id
    db.session.expire_all()
    return content_id


def chrome(data):
    """The header and footer, without the article between them."""
    head = data.split(b'<main')[0]
    foot = data.rsplit(b'</main>', 1)[-1]
    return head, foot


def test_the_ordinary_page_is_left_alone(app, client, acme, user):
    """The lock belongs to preview and nowhere else: a reader on the real
    site must still be able to use it.

    The front page, because that is the surface the theme always draws.
    The community surface renders the shell's own header instead, which
    never had a lock to leak.

    Looped rather than parametrized: the theme list is filled in when the
    application boots, and at import time it is still empty.
    """
    for theme in AVAILABLE_THEMES:
        acme.theme = theme
        db.session.commit()
        page = client.get('/', base_url=ACME)
        assert page.status_code == 200, theme
        head, foot = chrome(page.data)
        assert b'<header' in head, theme
        assert b'inert' not in head, theme
        assert b'inert' not in foot, theme


def test_the_lock_draws_no_box_of_its_own(app, client, acme, user):
    """The wrapper that carries the lock is `contents`, so it produces no
    box. Without that it becomes the containing block for whatever it
    wraps, and a sticky header (Supremely ships one) stops sticking during
    a preview -- a preview showing the piece as it will not be."""
    acme.theme = 'supremely'
    db.session.commit()
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    body = client.get(f'/manage/content/{content_id}/preview',
                      base_url=ACME).data
    assert b'<div inert' in body
    assert b'<div inert class="contents">' in body
    assert body.count(b'<div inert') == body.count(b'<div inert class="contents">')


def test_the_way_out_sits_outside_the_lock(app, client, acme, user):
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    body = client.get(f'/manage/content/{content_id}/preview',
                      base_url=ACME).data
    assert b'Back to the editor' in body
    assert f'/manage/content/{content_id}/edit'.encode() in body
    # Between the two switched-off ends, not inside either. In there, the
    # one link that still goes anywhere would go off with the rest.
    assert (body.index(b'</header>')
            < body.index(b'Back to the editor')
            < body.index(b'<footer'))


def test_the_banner_says_why_nothing_responds(app, client, acme, user):
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    page = client.get(f'/manage/content/{content_id}/preview', base_url=ACME)
    assert b'switched off' in page.data


def test_an_unsaved_draft_is_told_to_close_the_tab(app, client, acme, user):
    # There is no editor address for an item that was never written down,
    # and preview opens in a tab of its own, so that is the way back.
    login_as(client, user)
    page = client.post('/manage/content/article/preview', base_url=ACME,
                       data={'title': 'Not saved yet', 'slug': '',
                             'body': 'Draft body.', 'visibility': 'public'})
    assert page.status_code == 200
    assert b'Close this tab' in page.data
    assert b'Back to the editor' not in page.data


def test_the_return_path_is_switched_off_as_well(app, client, acme, user):
    """The archive back-link is navigation that sits inside the content
    column, where the lock on the header and footer does not reach. It is
    also the link most likely to be clicked by mistake, being an arrow
    pointing away from the draft directly under the banner saying not to."""
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    page = client.get(f'/manage/content/{content_id}/preview', base_url=ACME)
    middle = page.data.split(b'<main')[1].split(b'</main>')[0]
    assert b'href="/blog"' in middle, 'no back-link on the page to check'
    back = middle.split(b'href="/blog"')[1].split(b'>')[0]
    assert b'inert' in back


def test_the_body_of_the_draft_is_left_alone(app, client, acme, user):
    """The lock is on navigation, not on the draft. A preview exists to
    show the piece as it will be, and its own links are part of that."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Field Notes', slug='field-notes',
                       body='Read [the other one](/blog/other).',
                       org_id=acme.id, visibility='public', fields={},
                       tags=[], created_by_id=user.id)
        item.save()
        item.publish()
        content_id = item.id
    db.session.expire_all()
    login_as(client, user)
    page = client.get(f'/manage/content/{content_id}/preview', base_url=ACME)
    middle = page.data.split(b'<main')[1].split(b'</main>')[0]
    assert b'href="/blog/other"' in middle, 'the draft rendered no link'
    link = middle.split(b'href="/blog/other"')[1].split(b'>')[0]
    assert b'inert' not in link


def test_the_editors_own_preview_button_offers_the_way_back(app, client,
                                                            acme, user):
    """That button posts the form rather than following a link, so what
    renders is a throwaway row built from the fields, with no id of its
    own. The stored item's id has to be carried in beside it, or a saved
    piece previewed from the editor has no way back to the editor."""
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    page = client.post(f'/manage/content/{content_id}/preview',
                       base_url=ACME,
                       data={'title': 'Edited but not saved',
                             'slug': 'field-notes', 'body': 'New words.',
                             'visibility': 'public'})
    assert page.status_code == 200
    assert b'Edited but not saved' in page.data
    assert b'Back to the editor' in page.data
    assert f'/manage/content/{content_id}/edit'.encode() in page.data
    assert b'Close this tab' not in page.data
