"""The way into the editor from the article itself.

An author had to go and find their own piece in the console to change a
word in it. The item now carries its own Edit button, and the button says
whose work it is: your own is an ordinary edit, somebody else's is an
administrator reaching in.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Content, Membership
from tests.conftest import enable_types, login_as, make_user

ACME = 'http://acme.example.test'


def publish(app, org, author_id, title='Field Notes', slug='field-notes'):
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type='article', title=title, slug=slug,
                       body='Words.', org_id=org.id, visibility='public',
                       fields={}, tags=[], created_by_id=author_id)
        item.save()
        item.publish()
        content_id = item.id
    db.session.expire_all()
    return content_id


def admin_client(app, org, email='ada@example.com'):
    """A second admin: someone who may edit anything, but wrote none of it."""
    other = make_user(email=email, name='Ada')
    Membership.add(other.id, org.id, role='admin')
    client = app.test_client()
    return login_as(client, other), other


def edit_link(data, content_id):
    """The Edit anchor for this item, attributes and all."""
    href = f'/manage/content/{content_id}/edit'.encode()
    assert href in data, 'no way into the editor on the page'
    return data.split(href)[1].split(b'</a>')[0]


def test_an_author_gets_a_plain_edit_button_on_their_own_article(app, client,
                                                                 acme, user):
    content_id = publish(app, acme, user.id)
    login_as(client, user)
    link = edit_link(client.get('/blog/field-notes', base_url=ACME).data,
                     content_id)
    assert b'btn-secondary' in link
    assert b'btn-admin' not in link
    assert b'admin only' not in link


def test_another_admin_gets_the_same_button_marked(app, acme, user):
    content_id = publish(app, acme, user.id)
    other, _ = admin_client(app, acme)
    link = edit_link(other.get('/blog/field-notes', base_url=ACME).data,
                     content_id)
    # Amber: this is somebody else's article.
    assert b'btn-admin' in link
    assert b'admin only' in link


def test_a_member_is_offered_no_way_into_the_editor(app, acme, user):
    # A member has no content.write, so the editor would turn them away.
    content_id = publish(app, acme, user.id)
    member = make_user(email='plain@example.com')
    Membership.add(member.id, acme.id, role='member')
    client = app.test_client()
    login_as(client, member)
    page = client.get('/blog/field-notes', base_url=ACME).data
    assert f'/manage/content/{content_id}/edit'.encode() not in page


def test_a_visitor_is_offered_no_way_into_the_editor(app, client, acme, user):
    content_id = publish(app, acme, user.id)
    page = client.get('/blog/field-notes', base_url=ACME).data
    assert f'/manage/content/{content_id}/edit'.encode() not in page


def test_the_theme_renders_the_button_too(app, client, acme, user):
    # An item whose type presents through the theme (a team member is
    # brochure furniture, not community activity) never reaches the shell
    # template, so the theme has to carry the button as well.
    enable_types(acme, 'team_member')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        person = Content(type='team_member', title='Ada Lovelace',
                         slug='ada-lovelace', body='Engineer.',
                         org_id=acme.id, visibility='public', fields={},
                         tags=[], created_by_id=user.id)
        person.save()
        person.publish()
        content_id = person.id
    db.session.expire_all()
    login_as(client, user)
    page = client.get('/team/ada-lovelace', base_url=ACME)
    assert page.status_code == 200
    assert b'btn-secondary' in edit_link(page.data, content_id)


def test_the_theme_offers_a_visitor_nothing(app, client, acme, user):
    enable_types(acme, 'team_member')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        person = Content(type='team_member', title='Ada Lovelace',
                         slug='ada-lovelace', body='Engineer.',
                         org_id=acme.id, visibility='public', fields={},
                         tags=[], created_by_id=user.id)
        person.save()
        person.publish()
        content_id = person.id
    db.session.expire_all()
    page = client.get('/team/ada-lovelace', base_url=ACME)
    assert page.status_code == 200
    assert f'/manage/content/{content_id}/edit'.encode() not in page.data


def test_the_button_never_crosses_communities(app, acme, globex):
    # An admin of one community, looking at another's article. Whatever
    # they are shown, it must not be an author's plain button.
    content_id = publish(app, acme, acme.memberships[0].user_id)
    hank = globex.memberships[0].user
    client = app.test_client()
    login_as(client, hank)
    page = client.get('/blog/field-notes', base_url=ACME)
    assert page.status_code == 200
    assert f'/manage/content/{content_id}/edit'.encode() not in page.data


def test_a_platform_admin_who_is_not_a_member_is_offered_nothing(
        app, client, acme, platform_admin):
    # Being an administrator of the installation is not the same as
    # belonging to this community, and the editor turns them away too.
    content_id = publish(app, acme, acme.memberships[0].user_id)
    login_as(client, platform_admin)
    page = client.get('/blog/field-notes', base_url=ACME)
    assert page.status_code == 200
    assert f'/manage/content/{content_id}/edit'.encode() not in page.data
    assert client.get(f'/manage/content/{content_id}/edit',
                      base_url=ACME).status_code == 403


def test_content_nobody_wrote_is_somebody_elses(app, client, acme, user):
    # Provisioning seeds items with no author recorded. An admin editing
    # one is not editing their own work, so the button says so.
    content_id = publish(app, acme, None)
    login_as(client, user)
    link = edit_link(client.get('/blog/field-notes', base_url=ACME).data,
                     content_id)
    assert b'btn-admin' in link


def test_an_unsaved_preview_draws_no_edit_button(app, client, acme, user):
    # The editor's preview renders a throwaway row that was never written
    # down, so there is no id to address the editor by. Asking for one
    # anyway used to fail the whole page.
    login_as(client, user)
    page = client.post('/manage/content/article/preview', base_url=ACME,
                       data={'csrf_token': 'x', 'title': 'Draft',
                             'body': 'Not saved.'})
    assert page.status_code == 200
    assert b'Draft' in page.data
    assert b'/edit"' not in page.data


@pytest.mark.parametrize('theme', ('origin', 'supremely', 'midnight',
                                   'trailhead'))
def test_every_bundled_theme_carries_the_button(app, client, acme, user,
                                                theme):
    """A theme that overrides page.html and forgets the call takes the
    button away with it, and it looks like a working page. The affordance
    cannot come and go with the theme picker."""
    acme.theme = theme
    db.session.commit()
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='Our Story', slug='story',
                       body='Our story.', org_id=acme.id, visibility='public',
                       fields={}, tags=[], created_by_id=user.id,
                       presentation='site')
        page.save()
        page.publish()
        content_id = page.id
    db.session.expire_all()
    login_as(client, user)
    body = client.get('/story', base_url=ACME).data
    assert b'btn-secondary' in edit_link(body, content_id)


def test_a_slug_bound_template_carries_it_too(app, client, acme, user):
    # page-presskit.html is bound to one slug and draws its own chrome, so
    # it is exactly the kind of template that gets missed.
    acme.theme = 'supremely'
    db.session.commit()
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='Press Kit', slug='presskit',
                       body='Logos and colours.', org_id=acme.id,
                       visibility='public', fields={}, tags=[],
                       created_by_id=user.id, presentation='site')
        page.save()
        page.publish()
        content_id = page.id
    db.session.expire_all()
    login_as(client, user)
    assert b'btn-secondary' in edit_link(
        client.get('/presskit', base_url=ACME).data, content_id)


@pytest.mark.parametrize('presentation', ('site', 'community'))
def test_a_page_carries_the_button_too(app, client, acme, user, presentation):
    """A page renders through its own template rather than the single-item
    one, and it is the thing people edit most. Both templates are in play:
    the "Appears" setting sends a page to the theme or to the shell, and a
    button on only one of them would come and go with a select box."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='Our Story', slug='story',
                       body='Our story.', org_id=acme.id,
                       visibility='public', fields={}, tags=[],
                       created_by_id=user.id, presentation=presentation)
        page.save()
        page.publish()
        content_id = page.id
    db.session.expire_all()
    login_as(client, user)
    body = client.get('/story', base_url=ACME).data
    # The shell draws its rail; the theme does not. Proves which template
    # actually rendered, so neither case can quietly pass for the other.
    assert (b'id="rail"' in body) == (presentation == 'community')
    assert b'btn-secondary' in edit_link(body, content_id)


def test_an_author_demoted_to_member_loses_the_button(app, acme, user):
    # The editor asks for content.write, not authorship. A button the
    # editor would refuse is worse than no button.
    content_id = publish(app, acme, user.id)
    writer = make_user(email='wren@example.com', name='Wren')
    Membership.add(writer.id, acme.id, role='admin')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = db.session.get(Content, content_id)
        item.created_by_id = writer.id
        membership = Membership.query.filter_by(user_id=writer.id).one()
        membership.role = 'member'
        db.session.commit()
    client = app.test_client()
    login_as(client, writer)
    page = client.get('/blog/field-notes', base_url=ACME)
    assert f'/manage/content/{content_id}/edit'.encode() not in page.data
    # ...and the editor would indeed have refused them.
    assert client.get(f'/manage/content/{content_id}/edit',
                      base_url=ACME).status_code == 403
