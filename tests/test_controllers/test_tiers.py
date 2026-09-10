"""Manage → Tiers, and the tier on a membership.

The ladder is a list an administrator reorders, so it has a page of its
own. Who sits on which rung is set beside the role on the Members page,
because that is where somebody is already looking when they think about it.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Membership, Tier
from app.models.invitation import Invitation
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def add_tier(app, org, name, slug):
    with app.test_request_context(base_url=ACME):
        g.org = org
        return Tier.add(name=name, slug=slug).id


def plain_member(app, org, email='mel@example.com'):
    user = make_user(email=email)
    with app.test_request_context(base_url=ACME):
        g.org = org
        Membership.add(user.id, org.id)
    return user


def tiers_of(app, org, include_retired=True):
    with app.test_request_context(base_url=ACME):
        g.org = org
        return [(t.slug, t.rank, t.is_active)
                for t in Tier.in_order(include_retired=include_retired)]


# --- the page -----------------------------------------------------------

def test_the_page_lists_the_ladder_bottom_first(app, client, acme, user):
    add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    page = client.get('/manage/tiers', base_url=ACME)
    assert page.status_code == 200
    body = page.data
    # Names sit in the rename field's value, references in their own cell.
    assert body.index(b'value="Free"') < body.index(b'value="Pro"')
    assert body.index(b'>free<') < body.index(b'>pro<')


def test_a_member_cannot_reach_the_page(app, acme):
    member = plain_member(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.get('/manage/tiers', base_url=ACME).status_code == 403


def test_a_tier_is_added_on_top(app, client, acme, user):
    login_as(client, user)
    client.post('/manage/tiers', base_url=ACME,
                data={'name': 'Pro', 'slug': 'pro'})
    assert tiers_of(app, acme) == [('free', 1, True), ('pro', 2, True)]


def test_a_duplicate_slug_is_refused(app, client, acme, user):
    add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post('/manage/tiers', base_url=ACME,
                data={'name': 'Pro again', 'slug': 'pro'})
    assert len(tiers_of(app, acme)) == 2


def test_renaming_leaves_the_reference_alone(app, client, acme, user):
    """The slug is what every gated item points at, so a rename must not
    touch it. The form does not offer it at all."""
    tier_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post(f'/manage/tiers/{tier_id}/rename', base_url=ACME,
                data={'name': 'Crew', 'slug': 'crew'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        tier = db.session.get(Tier, tier_id)
        assert (tier.name, tier.slug) == ('Crew', 'pro')


def test_moving_a_tier_reorders_the_ladder(app, client, acme, user):
    add_tier(app, acme, 'Supporter', 'supporter')
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post(f'/manage/tiers/{pro_id}/move', base_url=ACME,
                data={'direction': 'up'})
    assert [slug for slug, _, _ in tiers_of(app, acme)] == [
        'free', 'pro', 'supporter']


def test_retiring_and_bringing_a_tier_back(app, client, acme, user):
    tier_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post(f'/manage/tiers/{tier_id}/toggle', base_url=ACME)
    assert ('pro', 2, False) in tiers_of(app, acme)
    client.post(f'/manage/tiers/{tier_id}/toggle', base_url=ACME)
    assert ('pro', 2, True) in tiers_of(app, acme)


def test_the_last_tier_cannot_be_retired(app, client, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        free_id = Tier.by_slug('free').id
    login_as(client, user)
    client.post(f'/manage/tiers/{free_id}/toggle', base_url=ACME)
    assert tiers_of(app, acme) == [('free', 1, True)]


def test_another_communitys_tier_is_not_reachable(app, acme, globex, user):
    """The route resolves acme; the id names a globex row. A 404 rather
    than a message, because acme has no business knowing it exists."""
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        foreign_id = Tier.by_slug('free').id
    client = app.test_client()
    login_as(client, user)
    assert client.post(f'/manage/tiers/{foreign_id}/rename', base_url=ACME,
                       data={'name': 'Taken'}).status_code == 404


# --- the tier on a membership -------------------------------------------

def test_an_admin_moves_a_member_between_tiers(app, client, acme, user):
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    member = plain_member(app, acme)
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
    client.post(f'/manage/members/{membership_id}/tier', base_url=ACME,
                data={'tier_id': pro_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Membership.get(member.id, acme.id).tier.slug == 'pro'


def test_an_admin_may_change_their_own_tier(app, client, acme, user):
    """A tier is not a permission, so there is nothing to escalate: an
    administrator already reads everything whatever rung they are on."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        own_id = Membership.get(user.id, acme.id).id
    client.post(f'/manage/members/{own_id}/tier', base_url=ACME,
                data={'tier_id': pro_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Membership.get(user.id, acme.id).tier.slug == 'pro'


def test_a_member_cannot_move_themselves(app, acme):
    member = plain_member(app, acme)
    client = app.test_client()
    login_as(client, member)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
    assert client.post(f'/manage/members/{membership_id}/tier',
                       base_url=ACME, data={'tier_id': 1}).status_code == 403


def test_a_tier_from_another_community_is_refused(app, client, acme, globex,
                                                  user):
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        foreign_id = Tier.by_slug('free').id
    member = plain_member(app, acme)
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
    client.post(f'/manage/members/{membership_id}/tier', base_url=ACME,
                data={'tier_id': foreign_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Membership.get(member.id, acme.id).tier.slug == 'free'


# --- invitations --------------------------------------------------------

def test_an_invitation_carries_a_tier_to_the_person_accepting(app, client,
                                                              acme, user):
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post('/manage/invitations', base_url=ACME,
                data={'email': '', 'role': 'member', 'tier_id': pro_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        invitation = Invitation.query.order_by(Invitation.id.desc()).first()
        assert invitation.tier_id == pro_id
        newcomer = make_user(email='new@example.com')
        invitation.accept(newcomer)
        assert Membership.get(newcomer.id, acme.id).tier.slug == 'pro'


def test_an_invitation_with_no_tier_lands_on_the_bottom_rung(app, acme):
    """An invitation written before tiers existed. That is the only way
    tier_id is null: a tier is retired rather than deleted, and SET NULL
    fires only on a delete."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        invitation, _ = Invitation.create(acme.id, role='member')
        assert invitation.tier_id is None
        newcomer = make_user(email='new@example.com')
        invitation.accept(newcomer)
        assert Membership.get(newcomer.id, acme.id).tier.slug == 'free'


def test_adding_an_existing_user_puts_them_on_the_chosen_tier(app, client,
                                                              acme, user):
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    outsider = make_user(email='outsider@example.com')
    login_as(client, user)
    client.post('/manage/members/add', base_url=ACME,
                data={'email': 'outsider@example.com', 'role': 'member',
                      'tier_id': pro_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Membership.get(outsider.id, acme.id).tier.slug == 'pro'


# --- the page does not leak the ladder ----------------------------------

@pytest.mark.parametrize('path', ('/members', '/dashboard'))
def test_a_members_tier_is_not_shown_to_other_members(app, acme, path):
    """A tier is between the member and the organization. Nothing on the
    community surface says who is on which rung.

    The member being looked at is actually on Pro, which is the only way
    this can fail: a page printing everyone's tier would print "Free" for
    a fixture where nobody holds anything else, and pass.
    """
    # A name nothing else on either page happens to contain, so the
    # assertion can only fail for the reason it names.
    pro_id = add_tier(app, acme, 'Zephyr', 'zephyr')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.update_settings(member_directory=True)
        db.session.commit()
    shown = plain_member(app, acme, email='shown@example.com')
    looker = plain_member(app, acme, email='looker@example.com')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(shown.id, acme.id).set_tier(
            db.session.get(Tier, pro_id))

    client = app.test_client()
    login_as(client, looker)
    body = client.get(path, base_url=ACME).data
    assert b'Zephyr' not in body


# --- the tier column on the members page --------------------------------

def tier_cell(body, membership_id):
    """The tier select for one member's row."""
    marker = f'/manage/members/{membership_id}/tier'.encode()
    assert marker in body, 'no tier control on that row'
    return body.split(marker)[1].split(b'</form>')[0]


def test_the_row_shows_the_tier_the_member_is_on(app, client, acme, user):
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    member = plain_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
        Membership.get(member.id, acme.id).set_tier(
            db.session.get(Tier, pro_id))
    login_as(client, user)
    cell = tier_cell(client.get('/manage/members', base_url=ACME).data,
                     membership_id)
    assert f'value="{pro_id}" selected'.encode() in cell


def test_a_member_on_a_retired_tier_still_shows_it(app, client, acme, user):
    """Offer only the active rungs and this select comes up with nothing
    chosen. The browser then posts the first option, and one Save on an
    unrelated part of the row moves the member off the tier they were on.
    """
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    member = plain_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
        Membership.get(member.id, acme.id).set_tier(
            db.session.get(Tier, pro_id))
        db.session.get(Tier, pro_id).retire()

    login_as(client, user)
    cell = tier_cell(client.get('/manage/members', base_url=ACME).data,
                     membership_id)
    assert f'value="{pro_id}" selected'.encode() in cell
    assert b'selected' in cell


def test_a_retired_tier_is_not_offered_to_anybody_else(app, client, acme,
                                                       user):
    """It shows on the row of whoever holds it, and nowhere else: not on
    another member's row, not on the invitation form."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    member = plain_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
        db.session.get(Tier, pro_id).retire()
    login_as(client, user)
    body = client.get('/manage/members', base_url=ACME).data
    assert f'value="{pro_id}"'.encode() not in tier_cell(body, membership_id)
    invite = body.split(b'/manage/invitations')[1].split(b'</form>')[0]
    assert f'value="{pro_id}"'.encode() not in invite


def test_a_tier_id_that_is_not_a_number_is_refused(app, client, acme, user):
    """The value comes from a form, and a form is whatever the browser
    sends. isdigit() is true of Unicode digits int() will not take."""
    member = plain_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
    login_as(client, user)
    for value in ('\u00b2', 'nine', '9' * 40):
        page = client.post(f'/manage/members/{membership_id}/tier',
                           base_url=ACME, data={'tier_id': value},
                           follow_redirects=True)
        assert page.status_code == 200, value


def test_an_invitation_honours_a_tier_retired_before_it_was_accepted(
        app, acme):
    """Retiring stops a tier being offered afresh. It does not revoke it
    from somebody already promised it."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        invitation, _ = Invitation.create(acme.id, role='member',
                                          tier_id=pro_id)
        db.session.get(Tier, pro_id).retire()
        newcomer = make_user(email='late@example.com')
        invitation.accept(newcomer)
        assert Membership.get(newcomer.id, acme.id).tier.slug == 'pro'


# --- the Tiers page itself ----------------------------------------------

def test_the_page_counts_who_is_on_each_rung(app, client, acme, user):
    """Retiring a tier people hold is a different decision from retiring
    an empty one, which is the whole reason the column is there."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    for n in range(2):
        member = plain_member(app, acme, email=f'p{n}@example.com')
        with app.test_request_context(base_url=ACME):
            g.org = acme
            Membership.get(member.id, acme.id).set_tier(
                db.session.get(Tier, pro_id))
    login_as(client, user)
    body = client.get('/manage/tiers', base_url=ACME).data
    row = body.split(b'value="Pro"')[1].split(b'</tr>')[0]
    assert b'>2<' in row, 'the Pro row does not say two members'
    free_row = body.split(b'value="Free"')[1].split(b'</tr>')[0]
    assert b'>1<' in free_row, 'the Free row should hold the owner alone'


def test_a_retired_tier_stays_on_the_page(app, client, acme, user):
    """This is the only page that can bring one back. A retired tier that
    fell off it could never be restored, which would make "retired, never
    deleted" a promise the console cannot keep."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    login_as(client, user)
    client.post(f'/manage/tiers/{pro_id}/toggle', base_url=ACME)
    body = client.get('/manage/tiers', base_url=ACME).data
    assert b'value="Pro"' in body
    assert b'Bring back' in body


def test_a_retired_tier_is_refused_for_somebody_new(app, client, acme, user):
    """The templates stop offering it; a crafted post must be refused too.
    Not a flat ban: the row of whoever already stands on it has to keep
    working, which the next test covers."""
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    outsider = make_user(email='outsider@example.com')
    login_as(client, user)
    client.post(f'/manage/tiers/{pro_id}/toggle', base_url=ACME)
    client.post('/manage/members/add', base_url=ACME,
                data={'email': 'outsider@example.com', 'role': 'member',
                      'tier_id': pro_id})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership = Membership.get(outsider.id, acme.id)
        assert membership is None or membership.tier.slug == 'free'


def test_the_holder_of_a_retired_tier_can_still_be_saved(app, client, acme,
                                                         user):
    pro_id = add_tier(app, acme, 'Pro', 'pro')
    member = plain_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership_id = Membership.get(member.id, acme.id).id
        Membership.get(member.id, acme.id).set_tier(
            db.session.get(Tier, pro_id))
    login_as(client, user)
    client.post(f'/manage/tiers/{pro_id}/toggle', base_url=ACME)
    page = client.post(f'/manage/members/{membership_id}/tier',
                       base_url=ACME, data={'tier_id': pro_id},
                       follow_redirects=True)
    # They stay on it either way, being already there; what tells the two
    # apart is whether the save was accepted or refused.
    assert b'not offered' not in page.data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Membership.get(member.id, acme.id).tier.slug == 'pro'


def test_an_id_too_large_for_the_column_is_a_404(app, client, acme, user):
    """Flask's int converter has no ceiling, so without a bound the value
    reaches the driver and raises there: a 500 for what is plainly a
    request for something that does not exist."""
    login_as(client, user)
    huge = '9' * 40
    for path in (f'/manage/tiers/{huge}/rename', f'/manage/tiers/{huge}/toggle',
                 f'/manage/tiers/{huge}/move', f'/manage/members/{huge}/tier'):
        assert client.post(path, base_url=ACME).status_code == 404, path
