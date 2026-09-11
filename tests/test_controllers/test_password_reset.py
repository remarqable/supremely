"""Issuing a reset link from Manage, and redeeming one.

The routes either side of the model rules: who may press the button, and
what somebody holding a link can do with it.
"""

import re

from app.extensions import db
from app.models import Membership, PasswordReset, User
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'
PASSWORD = 'a-brand-new-password-1'


def _member_of(org, email='ada@example.com', role='member', name='Ada'):
    user = make_user(email=email, name=name)
    membership = Membership.add(user.id, org.id, role=role)
    return user, membership


def _issue(client, membership_id, base_url=ACME):
    """Press the button. The link comes back on a page of its own, not
    through a flash, so there is nothing to follow."""
    return client.post(f'/manage/members/{membership_id}/reset-password',
                       base_url=base_url, follow_redirects=True)


def _link_in(response) -> str | None:
    """The one-time link out of the panel that shows it once."""
    return _link_in_page(response.get_data(as_text=True))


def _link_in_page(page: str) -> str | None:
    found = re.search(r'value="(http[^"]*/reset/[^"]+)"', page)
    return found.group(1) if found else None


def _path_of(url: str) -> str:
    return url.split('example.test', 1)[1]


def test_an_owner_can_hand_a_member_a_way_back_in(app, client, acme, user):
    """The whole point: an installation with no email, and somebody locked
    out of it."""
    login_as(client, user)
    _, membership = _member_of(acme)

    response = _issue(client, membership.id)
    assert response.status_code == 200
    assert _link_in(response) is not None
    assert PasswordReset.query.count() == 1


def test_the_link_sets_a_password_and_signs_them_in(app, client, acme, user):
    login_as(client, user)
    member, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    visitor = app.test_client()
    assert visitor.get(link, base_url=ACME).status_code == 200
    response = visitor.post(link, base_url=ACME,
                            data={'password': PASSWORD,
                                  'password_confirm': PASSWORD})

    assert response.status_code == 302
    assert User.query.filter_by(id=member.id).first().check_password(PASSWORD)
    with visitor.session_transaction() as session:
        assert session.get('_user_id')


def test_the_link_stops_working_after_one_use(app, client, acme, user):
    login_as(client, user)
    _, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    visitor = app.test_client()
    visitor.post(link, base_url=ACME, data={'password': PASSWORD,
                                            'password_confirm': PASSWORD})
    assert visitor.get(link, base_url=ACME).status_code == 404


def test_two_passwords_that_disagree_are_refused(app, client, acme, user):
    login_as(client, user)
    member, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))
    before = User.query.filter_by(id=member.id).first().password_hash

    visitor = app.test_client()
    visitor.post(link, base_url=ACME, data={'password': PASSWORD,
                                            'password_confirm': 'something-else-9'})

    assert User.query.filter_by(id=member.id).first().password_hash == before
    assert visitor.get(link, base_url=ACME).status_code == 200


def test_a_member_cannot_issue_one(app, acme):
    """members.manage is an owner and admin permission. A member pressing
    this would be a member resetting anyone's password."""
    plain, _ = _member_of(acme, email='plain@example.com')
    _, membership = _member_of(acme, email='victim@example.com')
    client = login_as(app.test_client(), plain)

    response = client.post(f'/manage/members/{membership.id}/reset-password',
                           base_url=ACME)
    # The exact code, not a set that happens to contain 302 -- which is
    # also what this route returns when it succeeds.
    assert response.status_code == 403
    assert PasswordReset.query.count() == 0


def test_a_visitor_cannot_issue_one(app, client, acme):
    _, membership = _member_of(acme)
    response = client.post(f'/manage/members/{membership.id}/reset-password',
                           base_url=ACME)
    assert response.status_code == 302       # to the login page
    assert '/auth/login' in response.headers['Location']
    assert PasswordReset.query.count() == 0


def test_an_admin_cannot_reset_an_owner(app, acme, user):
    """The guard that stops an admin suspending an owner stops this too.
    Without it, every admin is one click from being the owner."""
    admin, _ = _member_of(acme, email='admin@example.com', role='admin')
    owner_membership = Membership.query.filter_by(
        user_id=user.id, org_id=acme.id).first()
    client = login_as(app.test_client(), admin)

    _issue(client, owner_membership.id)
    assert PasswordReset.query.count() == 0


def test_nobody_resets_their_own_password_here(app, client, acme, user):
    """Change password is the door for that, and this one ends the session
    they are standing in."""
    login_as(client, user)
    own = Membership.query.filter_by(user_id=user.id, org_id=acme.id).first()

    _issue(client, own.id)
    assert PasswordReset.query.count() == 0


def test_another_organizations_member_cannot_be_reached(app, acme, globex, user):
    """The membership id comes from a URL. One belonging to another
    organization must not resolve."""
    _, membership = _member_of(globex, email='hank2@example.com')
    client = login_as(app.test_client(), user)

    response = client.post(
        f'/manage/members/{membership.id}/reset-password', base_url=ACME)
    assert response.status_code == 404
    assert PasswordReset.query.count() == 0


def test_a_link_is_not_redeemable_on_another_organizations_address(
        app, client, acme, globex, user):
    login_as(client, user)
    _, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    visitor = app.test_client()
    assert visitor.get(link, base_url=GLOBEX).status_code == 404


def test_the_page_never_says_whose_account_it_is(app, client, acme, user):
    """Anyone holding the link can open this page, and the link travels
    through chat and conversation. It should not confirm an address."""
    login_as(client, user)
    member, membership = _member_of(acme, email='private@example.com',
                                    name='Wilhelmina Ravensworth')
    link = _path_of(_link_in(_issue(client, membership.id)))

    page = app.test_client().get(link, base_url=ACME).get_data(as_text=True)
    assert member.email not in page
    assert member.name not in page


def test_the_token_is_not_written_to_the_log(app, client, acme, user, caplog):
    """A link in a log file is a link, and logs are read by more people and
    kept longer than the thing they describe."""
    login_as(client, user)
    _, membership = _member_of(acme)
    with caplog.at_level('INFO'):
        link = _link_in(_issue(client, membership.id))

    token = link.rsplit('/', 1)[1]
    assert token not in caplog.text
    assert 'password_reset_link_issued' in caplog.text


def test_issuing_a_second_link_retires_the_first(app, client, acme, user):
    login_as(client, user)
    _, membership = _member_of(acme)
    first = _path_of(_link_in(_issue(client, membership.id)))
    second = _path_of(_link_in(_issue(client, membership.id)))

    visitor = app.test_client()
    assert visitor.get(first, base_url=ACME).status_code == 404
    assert visitor.get(second, base_url=ACME).status_code == 200


def test_a_reset_ends_the_sessions_the_account_already_had(
        app, client, acme, user):
    """Somebody who was signed in on another device is signed out by the
    reset. That is the containment half of the feature."""
    login_as(client, user)
    member, membership = _member_of(acme)
    elsewhere = login_as(app.test_client(), member)
    assert elsewhere.get('/dashboard', base_url=ACME).status_code == 200

    link = _path_of(_link_in(_issue(client, membership.id)))
    app.test_client().post(link, base_url=ACME,
                           data={'password': PASSWORD,
                                 'password_confirm': PASSWORD})
    db.session.expire_all()

    assert elsewhere.get('/dashboard', base_url=ACME).status_code in (302, 401)


def test_where_there_is_email_the_member_gets_it_directly(app, client, acme, user):
    """The issue asks for the emailed path to stay the default where it
    works. The link is still shown, because the message may not arrive and
    the organizer is standing right there."""
    from app.platform import mailer
    from tests.conftest import configure_email

    configure_email(app)
    login_as(client, user)
    member, membership = _member_of(acme, email='ada@example.com')

    response = _issue(client, membership.id)

    assert _link_in(response) is not None
    assert [message for message in mailer._outbox
            if message['To'] == member.email], mailer._outbox


def test_the_email_carries_the_link_and_nothing_else_secret(
        app, client, acme, user):
    from app.platform import mailer
    from tests.conftest import configure_email

    configure_email(app)
    login_as(client, user)
    _, membership = _member_of(acme)
    link = _link_in(_issue(client, membership.id))

    message = mailer._outbox[0]
    assert link in message.get_body(('html',)).get_content()
    assert link in message.get_body(('plain',)).get_content()


def test_the_link_appears_once_and_only_in_its_own_panel(app, client, acme, user):
    """The generic flash partial draws every category it is not told to
    skip. It was drawing this one too, so a live way into somebody's
    account also appeared in a plain green success banner with none of the
    warning the real panel carries."""
    login_as(client, user)
    _, membership = _member_of(acme)

    page = _issue(client, membership.id).get_data(as_text=True)
    link = _link_in_page(page)

    assert page.count(link) == 1
    banner = page[:page.index(link)].rsplit('<div', 1)[-1]
    assert 'bg-green-50' not in banner


def test_a_deactivated_account_gets_no_link(app, client, acme, user):
    """A reset link is a way in. Issuing one for an account somebody
    switched off would make this the way to switch it back on."""
    login_as(client, user)
    member, membership = _member_of(acme)
    member.is_active = False
    member.save()
    db.session.commit()

    _issue(client, membership.id)
    assert PasswordReset.query.count() == 0


def test_the_refusal_does_not_say_which_rule_stopped_it(app, acme, globex, user):
    """Told apart, the message would confirm to an organizer that this
    person holds an account elsewhere on the installation, which is a fact
    about the rest of the installation and not theirs to learn."""
    client = login_as(app.test_client(), user)
    elsewhere, m_elsewhere = _member_of(acme, email='both@example.com')
    Membership.add(elsewhere.id, globex.id, role='owner')
    root, m_root = _member_of(acme, email='root2@example.com')
    root.is_platform_admin = True
    root.save()
    db.session.commit()

    first = _issue(client, m_elsewhere.id).get_data(as_text=True)
    second = _issue(client, m_root.id).get_data(as_text=True)

    message = 'cannot be reset from here'
    assert message in first and message in second
    assert 'another organization' not in first
    assert 'administers' not in second


def test_a_new_password_kills_every_outstanding_link(app, client, acme, user):
    """The link must not outlive the password it was issued to replace.
    Without this, the command the model offers as the answer to a stolen
    link -- flask users reset-password -- did not take it away, and neither
    did the member choosing a new password themselves."""
    login_as(client, user)
    member, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    member.set_password('chosen-by-the-member-2')
    member.save()
    db.session.commit()

    assert app.test_client().get(link, base_url=ACME).status_code == 404


def test_a_demoted_issuer_takes_their_links_with_them(app, acme, user):
    """An admin who goes rogue, is caught and is demoted keeps whatever
    links they made otherwise, which makes taking their access away not
    take their access away."""
    rogue, rogue_membership = _member_of(acme, email='rogue@example.com',
                                         role='admin')
    _, victim_membership = _member_of(acme, email='victim2@example.com')
    rogue_client = login_as(app.test_client(), rogue)
    link = _path_of(_link_in(_issue(rogue_client, victim_membership.id)))
    assert app.test_client().get(link, base_url=ACME).status_code == 200

    rogue_membership.change_role('member')
    db.session.commit()

    assert app.test_client().get(link, base_url=ACME).status_code == 404


def test_removing_the_member_takes_their_link_with_them(app, client, acme, user):
    """An organization that no longer has this member has no business
    holding a way into their account."""
    login_as(client, user)
    _, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    membership.remove()
    db.session.commit()

    assert app.test_client().get(link, base_url=ACME).status_code == 404


def test_a_suspended_member_cannot_be_issued_one(app, client, acme, user):
    login_as(client, user)
    _, membership = _member_of(acme)
    membership.suspend()
    db.session.commit()

    _issue(client, membership.id)
    assert PasswordReset.query.count() == 0


def test_a_refused_request_does_not_put_the_token_in_the_log(
        app, client, acme, user, caplog):
    """A CSRF refusal happens in before_request, so the link is still live
    when the refusal is logged. The app log is read by more people and kept
    longer than the chat message the link travelled in."""
    from app.middleware.ratelimit import _rate_limits

    login_as(client, user)
    _, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))
    token = link.rsplit('/', 1)[1]

    app.config['CSRF_ENABLED'] = True
    _rate_limits.clear()
    try:
        with caplog.at_level('WARNING'):
            refused = app.test_client().post(link, base_url=ACME,
                                             data={'password': PASSWORD})
    finally:
        app.config['CSRF_ENABLED'] = False
        _rate_limits.clear()

    assert refused.status_code == 403
    assert 'csrf_rejected' in caplog.text
    assert token not in caplog.text
    assert '<redacted>' in caplog.text
    # Refused before the view ran, so the link is untouched.
    assert app.test_client().get(link, base_url=ACME).status_code == 200


def test_the_account_holder_is_told(app, client, acme, user):
    """On an installation with no email this is the only signal they get."""
    from app.models import Notification

    login_as(client, user)
    member, membership = _member_of(acme)
    _issue(client, membership.id)

    notifications = Notification.query.filter_by(user_id=member.id).all()
    assert [n.type for n in notifications] == ['account.reset_issued']


def test_redeeming_does_not_hand_out_a_fortnight_long_cookie(
        app, client, acme, user):
    """Somebody handed a link in person is as likely to be at a borrowed
    keyboard as their own, and never asked to be remembered."""
    login_as(client, user)
    _, membership = _member_of(acme)
    link = _path_of(_link_in(_issue(client, membership.id)))

    visitor = app.test_client()
    visitor.post(link, base_url=ACME, data={'password': PASSWORD,
                                            'password_confirm': PASSWORD})

    assert not any(cookie.name == 'remember_token'
                   for cookie in visitor.cookie_jar) \
        if hasattr(visitor, 'cookie_jar') else True
    with visitor.session_transaction() as session:
        assert session.get('_user_id')
        assert not session.get('_remember')

