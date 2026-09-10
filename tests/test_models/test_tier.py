"""Membership tiers: the ladder, and the one rule that reads it.

A tier says what a member may read. What they may do is their role, and the
two never touch: an owner on the bottom tier still publishes, a member on
the top tier still cannot.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Content, DiscussionGroup, Membership, Tier
from app.platform.authz import (
    can_read,
    readable_visibilities,
    tier_ranks,
    tier_slug,
    visibility_choices,
    visibility_is_valid,
)
from app.platform.errors import ValidationError
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def ladder(app, org, *names):
    """Add tiers above the seeded Free one, bottom first."""
    with app.test_request_context(base_url=ACME):
        g.org = org
        made = [Tier.add(name=name, slug=name.lower()) for name in names]
        return [tier.id for tier in made]


def as_member(app, org, email='mel@example.com', role='member',
              on=None):
    """A member of this org, on a named tier. Returns the user."""
    user = make_user(email=email)
    with app.test_request_context(base_url=ACME):
        g.org = org
        tier = Tier.by_slug(on) if on else None
        Membership.add(user.id, org.id, role=role,
                       tier_id=tier.id if tier else None)
    return user


# --- the ladder --------------------------------------------------------

def test_a_new_community_has_one_tier(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        tiers = Tier.in_order()
        assert [(tier.name, tier.slug, tier.rank) for tier in tiers] == [
            ('Free', 'free', 1)]


def test_every_membership_sits_on_a_tier(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        owner = Membership.get(user.id, acme.id)
        assert owner.tier is not None
        assert owner.tier.slug == 'free'


def test_a_new_tier_goes_on_top(app, acme):
    ladder(app, acme, 'Supporter', 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert [(t.slug, t.rank) for t in Tier.in_order()] == [
            ('free', 1), ('supporter', 2), ('pro', 3)]


def test_moving_a_tier_renumbers_the_ladder(app, acme):
    ladder(app, acme, 'Supporter', 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.by_slug('pro').move(-1)
        assert [t.slug for t in Tier.in_order()] == [
            'free', 'pro', 'supporter']
        assert [t.rank for t in Tier.in_order()] == [1, 2, 3]


def test_a_move_settles_a_ladder_that_was_not_already_tidy(app, acme):
    """Ranks carry no unique constraint, so two tiers can share one and a
    ladder can arrive with gaps: from a seed, a migration, or an older
    version of this code. A move renumbers the whole ladder before it
    swaps, which is what makes the comparison in can_read mean anything.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier(org_id=acme.id, name='Supporter', slug='supporter', rank=7).save()
        Tier(org_id=acme.id, name='Pro', slug='pro', rank=7).save()
        Tier.by_slug('pro').move(-1)
        ranks = [t.rank for t in Tier.in_order()]
        assert ranks == [1, 2, 3], ranks
        assert [t.slug for t in Tier.in_order()] == [
            'free', 'pro', 'supporter']


def test_a_community_must_keep_one_tier(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        with pytest.raises(ValidationError):
            Tier.by_slug('free').retire()


def test_a_retired_tier_keeps_its_members_and_its_meaning(app, acme):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        pro = Tier.by_slug('pro')
        pro.retire()
        # Gone from every picker...
        assert 'pro' not in [t.slug for t in Tier.in_order()]
        assert 'tier:pro' not in [value for value, _ in visibility_choices()]
        # ...and still resolvable, so content requiring it still compares.
        assert Tier.by_slug('pro') is not None
        # ...and still on the ladder, so it keeps its place in the order.
        # A retired tier dropped from the ladder would renumber the ones
        # around it on the next move, quietly changing what every item
        # gated at those ranks requires.
        assert 'pro' in [t.slug for t in Tier.in_order(include_retired=True)]


def test_two_communities_may_both_have_a_pro(app, acme, globex):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        Tier.add(name='Pro', slug='pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert len(Tier.query.filter_by(slug='pro').all()) == 1


def test_a_slug_is_unique_within_a_community(app, acme):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        with pytest.raises(ValidationError):
            Tier.add(name='Pro again', slug='pro')


# --- the rule ----------------------------------------------------------

def test_the_prefix_is_read_in_one_place():
    assert tier_slug('tier:pro') == 'pro'
    assert tier_slug('members') is None
    assert tier_slug('public') is None
    assert tier_slug('tier:') is None


def test_public_needs_no_visitor(app):
    # Outside a request there is nobody to answer for, and only public
    # survives that.
    assert can_read('public') is True
    assert can_read('members') is False
    assert can_read('tier:pro') is False


def test_a_member_reads_up_to_their_own_tier(app, acme):
    ladder(app, acme, 'Supporter', 'Pro')
    member = as_member(app, acme, on='supporter')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(member.id, acme.id)
        assert g.membership.tier.slug == 'supporter'
        assert can_read('public') is True
        assert can_read('members') is True
        assert can_read('tier:free') is True
        assert can_read('tier:supporter') is True
        assert can_read('tier:pro') is False


def test_publishing_rights_read_everything(app, acme, user):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(user.id, acme.id)      # owner, on Free
        assert g.membership.tier.slug == 'free'
        assert can_read('tier:pro') is True


def test_a_tier_that_resolves_to_nothing_fails_closed(app, acme):
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(member.id, acme.id)
        assert can_read('tier:nonesuch') is False


def test_the_sql_half_agrees_with_the_rule(app, acme):
    """readable_visibilities is can_read for a listing. The two answering
    differently is how a gated item appears in a grid and then refuses to
    open."""
    ladder(app, acme, 'Supporter', 'Pro')
    member = as_member(app, acme, on='supporter')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(member.id, acme.id)
        readable = readable_visibilities()
        for value in ('public', 'members', 'tier:free', 'tier:supporter'):
            assert can_read(value) is True and value in readable, value
        assert can_read('tier:pro') is False and 'tier:pro' not in readable


def test_an_administrator_is_not_enumerated(app, acme, user):
    # None, not a list: an admin reads content gated to a tier retired
    # years ago, and no list of today's tiers would hold it.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(user.id, acme.id)
        assert readable_visibilities() is None


# --- what carries a tier -----------------------------------------------

def test_content_accepts_a_tier_and_refuses_a_stranger(app, acme, user):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Gated', slug='gated',
                       body='x', org_id=acme.id, fields={}, tags=[],
                       visibility='tier:pro', created_by_id=user.id)
        item.save()
        assert item.visibility == 'tier:pro'
        item.visibility = 'tier:nonesuch'
        with pytest.raises(ValidationError):
            item.save()


def test_a_discussion_group_accepts_a_tier(app, acme):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = DiscussionGroup(name='Crew', slug='crew', org_id=acme.id,
                                visibility='tier:pro').save()
        assert group.visibility == 'tier:pro'


def test_the_area_switch_takes_a_tier(app, acme):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.update_settings(discussions_visibility='tier:pro')
        db.session.commit()
        assert DiscussionGroup.area_visibility() == 'tier:pro'


# --- the branches that must not be reordered ---------------------------

def test_a_visitor_reads_nothing_but_public(app, acme):
    """The guard that asks for a membership sits above the branch that
    answers `members`. Hoisting that branch, which reads like a harmless
    simplification, hands every members-only object to anyone at all."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = None
        assert can_read('public') is True
        assert can_read('members') is False
        assert can_read('tier:free') is False
        assert readable_visibilities() == ['public']


def test_a_listing_still_shows_what_a_retired_tier_gates(app, acme):
    """The listing half has to look at retired tiers as well.

    A member holding a tier above a retired one still reads what it gates.
    Leaving retired tiers out of this list is what makes an item appear in
    a grid and then refuse to open, which is the failure this pair of
    functions exists to prevent.
    """
    ladder(app, acme, 'Supporter', 'Pro')
    member = as_member(app, acme, on='pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.by_slug('supporter').retire()
        g.membership = Membership.get(member.id, acme.id)
        assert can_read('tier:supporter') is True
        assert 'tier:supporter' in readable_visibilities()


# --- retiring a tier leaves everything else alone ----------------------

def test_retiring_a_tier_does_not_lock_the_content_that_needs_it(app, acme,
                                                                 user):
    """Retiring is meant to stop a tier being offered, not to make every
    item already gated to it unsaveable. The picker and the validator are
    two different questions and only one of them narrows."""
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Gated', slug='gated', body='x',
                       org_id=acme.id, fields={}, tags=[],
                       visibility='tier:pro', created_by_id=user.id).save()
        group = DiscussionGroup(name='Crew', slug='crew', org_id=acme.id,
                                visibility='tier:pro').save()
        Tier.by_slug('pro').retire()

        item.title = 'Gated, and edited'
        item.save()                     # would raise before
        group.description = 'Still here'
        group.save()
        assert item.visibility == 'tier:pro'


def test_a_job_with_no_request_still_validates_a_tier(app, acme, user):
    """A worker runs under org_scope with no request and no g.org. Reading
    the organization from g alone finds none, and every tier-gated save
    made from a job would be refused as invalid."""
    from app.platform.tenant import org_scope
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        content_id = Content(
            type='article', title='Gated', slug='gated', body='x',
            org_id=acme.id, fields={}, tags=[], visibility='tier:pro',
            created_by_id=user.id).save().id
    with app.app_context(), org_scope(acme.id):
        item = db.session.get(Content, content_id)
        item.title = 'Edited by a job'
        item.save()


# --- assigning a tier ---------------------------------------------------

def test_an_admin_moves_a_member_between_tiers(app, acme):
    ladder(app, acme, 'Pro')
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership = Membership.get(member.id, acme.id)
        assert membership.tier.slug == 'free'
        membership.set_tier(Tier.by_slug('pro'))
        assert Membership.get(member.id, acme.id).tier.slug == 'pro'


def test_adding_a_member_to_another_community_lands_them_on_its_own_tier(
        app, acme, globex):
    """Adding somebody to one organization while another is in force.

    That is what an invitation accepted on one host for another
    organization does. Read through the tenant filter, the other
    organization's bottom tier is invisible, so a second Free is inserted
    beside the one already there and the new member lands on a duplicate.
    """
    outsider = make_user(email='outsider@example.com')
    with app.test_request_context(base_url=ACME):
        g.org = acme                     # acme in force, globex the target
        Membership.add(outsider.id, globex.id)
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        assert len(Tier.query.filter_by(slug='free').all()) == 1
        assert Membership.get(outsider.id, globex.id).tier.slug == 'free'


def test_one_community_cannot_reach_another_ones_ladder(app, acme, globex):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        assert Tier.by_slug('pro') is None
        assert [t.slug for t in Tier.in_order()] == ['free']


def test_the_last_tier_is_kept_even_with_no_organization_in_force(app, acme,
                                                                  globex):
    """The CLI, a job and a seed all run with the tenant filter standing
    down. An unpinned count totals every organization's tiers, so with two
    communities each holding one, each would be told it had two and allowed
    to retire its last."""
    from app.platform.tenant import unscoped
    with app.test_request_context(base_url=ACME):
        g.org = acme
        tier_id = Tier.by_slug('free').id
    with app.app_context():
        with unscoped():
            tier = db.session.get(Tier, tier_id)
        with pytest.raises(ValidationError):
            tier.retire()


# --- the two places the rule is asked from ------------------------------

def test_retiring_a_tier_does_not_reopen_the_discussions_area(app, client,
                                                              acme):
    """The org-wide switch holds a visibility value like anything else, and
    a retired tier is still a legal answer for it.

    Checked against the picker instead, a retired tier stops being a legal
    value, the switch falls back to per_group, and every public group in
    the area is readable again. A mistake here widens access rather than
    refusing a write, which is the direction that matters.
    """
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.update_settings(discussions_visibility='tier:pro')
        db.session.commit()
        Tier.by_slug('pro').retire()
        assert DiscussionGroup.area_visibility() == 'tier:pro'
        # A visitor is still shut out of the whole area.
        g.membership = None
        assert DiscussionGroup.area_readable_by_current_visitor() is False


def test_the_event_card_asks_what_this_member_may_read(app, acme, user):
    """The rail asked for the filter only when the viewer was not a member,
    which was the same question as "may they read it" only while any member
    could read anything. A tier between two members pulls those apart."""
    ladder(app, acme, 'Pro')
    member = as_member(app, acme)                   # on Free
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from datetime import date, timedelta
        # The seeded kickoff is dated today and would win the race for
        # "next", so it is put in the past and this is the only candidate.
        seeded = Content.query.filter_by(type='event').first()
        seeded.fields = {'starts_on': '2020-01-01'}
        db.session.commit()
        future = (date.today() + timedelta(days=30)).isoformat()
        event = Content(type='event', title='Crew call', slug='crew-call',
                        body='x', org_id=acme.id, fields={'starts_on': future},
                        tags=[], visibility='tier:pro',
                        created_by_id=user.id)
        event.save()
        event.publish()

        # A seeded public event already sits in the calendar, so the
        # question is which one comes back, not whether one does.
        g.membership = Membership.get(member.id, acme.id)
        assert Content.upcoming_event() is None
        g.membership.set_tier(Tier.by_slug('pro'))
        assert Content.upcoming_event().title == 'Crew call'


def test_the_announcement_card_asks_the_same_question(app, acme, user):
    """Through the page, not through the pieces. The card is a closure in
    the template context, and testing the query it ought to run rather than
    the card itself is how the card kept its old rule."""
    ladder(app, acme, 'Pro')
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='announcement', title='Only for Pro',
                       slug='only-for-pro', body='x', org_id=acme.id,
                       fields={}, tags=[], visibility='tier:pro',
                       created_by_id=user.id)
        item.save()
        item.publish()

    client = app.test_client()
    login_as(client, member)
    rail = client.get('/dashboard', base_url=ACME).data
    assert b'Only for Pro' not in rail
    # And the card shows the announcement they *can* read. Without the
    # filter the helper hands back the Pro one, the template's own can_view
    # blanks it, and the card falls to its empty state: nothing leaks, but
    # the reader loses the announcement that was meant for them.
    assert b'Welcome to Acme' in rail

    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Only for Pro' in client.get('/dashboard', base_url=ACME).data


def test_the_ladder_memo_does_not_outlive_its_request(app, acme, user):
    """The memo is keyed by organization, so it cannot hand one community
    another's ranks. What it can hand out is yesterday's ladder.

    Reordering is the way to see it: nothing about the member changes, only
    the ranks, so a memo held from the previous request answers with the
    order that reordering just replaced.
    """
    ladder(app, acme, 'Supporter', 'Pro')
    member = as_member(app, acme, on='supporter')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='announcement', title='Only for Pro',
                       slug='only-for-pro', body='x', org_id=acme.id,
                       fields={}, tags=[], visibility='tier:pro',
                       created_by_id=user.id)
        item.save()
        item.publish()

    client = app.test_client()
    login_as(client, member)
    # Supporter is rank 2, Pro rank 3: out of reach, and the ladder is now
    # in the memo.
    assert b'Only for Pro' not in client.get('/dashboard', base_url=ACME).data

    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.by_slug('pro').move(-1)     # Pro to rank 2, Supporter to 3
    assert b'Only for Pro' in client.get('/dashboard', base_url=ACME).data


def test_the_ladder_memo_is_cleared_when_a_request_begins(app, acme):
    """The guard itself, because a held app context is the only way to see
    it and the test client does not always share one.

    Every per-request memo on `g` is cleared before the request that would
    read it, for the reason the others give: `g` is application-context
    scoped, and a script or a test that holds one carries a memo into work
    it was not computed for. This one answers who may read what.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        g._tier_ranks = {acme.id: {'stale': 1}}
        app.preprocess_request()
        assert 'stale' not in tier_ranks()


def test_a_row_is_validated_against_its_own_community(app, acme, globex):
    """A model validating itself knows which organization it belongs to.
    Resolving the tier through whatever the request happened to resolve
    instead answers about the wrong community, or about none at all."""
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        Tier.add(name='Patron', slug='patron')
    with app.test_request_context(base_url=ACME):
        g.org = acme                     # acme in force, asking about globex
        assert visibility_is_valid('tier:patron', globex.id) is True
        assert visibility_is_valid('tier:patron', acme.id) is False
        # Without being told whose row it is, it answers about acme.
        assert visibility_is_valid('tier:patron') is False


def test_the_ladder_is_read_once_per_request(app, client, acme, user):
    """can_read runs per item, and a listing draws thirty of them. Read
    straight from the database that is thirty identical selects against a
    table with a handful of rows in it."""
    ladder(app, acme, 'Pro')
    member = as_member(app, acme)
    statements = []
    from sqlalchemy import event as sa_event
    engine = db.engine

    def record(conn, cursor, statement, params, context, many):
        if 'FROM tier' in statement:
            statements.append(statement)

    with app.test_request_context(base_url=ACME):
        g.org = acme
        g.membership = Membership.get(member.id, acme.id)
        sa_event.listen(engine, 'before_cursor_execute', record)
        try:
            for _ in range(30):
                can_read('tier:pro')
        finally:
            sa_event.remove(engine, 'before_cursor_execute', record)
    assert len(statements) <= 1, f'{len(statements)} tier queries for 30 reads'


def test_a_tier_locked_section_gates_every_item_in_it(app, acme, user):
    """The per-type lock is a visibility value like any other, so it takes
    a tier too. An item inside a locked section is gated whatever the item
    itself says, which is the rule the section lock has always had."""
    ladder(app, acme, 'Pro')
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('announcement', visibility='tier:pro')
        db.session.commit()

    client = app.test_client()
    login_as(client, member)
    # The seeded announcement is public, and still out of reach: the
    # section is what gates it.
    assert b'Welcome to Acme' not in client.get('/dashboard',
                                                base_url=ACME).data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Welcome to Acme' in client.get('/dashboard', base_url=ACME).data


# --- the three places an item is actually gated -------------------------

def gated_article(app, org, author_id, slug='crew-notes',
                  title='Crew notes', visibility='tier:pro'):
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type='article', title=title, slug=slug, body='Words.',
                       org_id=org.id, fields={}, tags=[],
                       visibility=visibility, created_by_id=author_id)
        item.save()
        item.publish()
    db.session.expire_all()
    return item


def test_a_member_below_the_tier_cannot_open_the_item(app, acme, user):
    """Content.visible_to_current_visitor is what the page asks. Tested
    through the page, because the rule reaching can_read is the whole
    point and a test of can_read alone does not see whether it does."""
    ladder(app, acme, 'Pro')
    gated_article(app, acme, user.id)
    member = as_member(app, acme)
    client = app.test_client()
    login_as(client, member)

    page = client.get('/blog/crew-notes', base_url=ACME)
    assert b'Words.' not in page.data          # the gate, not the body
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Words.' in client.get('/blog/crew-notes', base_url=ACME).data


def test_a_listing_does_not_show_what_it_would_refuse_to_open(app, acme,
                                                              user):
    """Content.visible_query is the listing half. With teasing off a gated
    item leaves the list entirely, and the list and the page have to agree
    about which items those are."""
    ladder(app, acme, 'Pro')
    gated_article(app, acme, user.id)
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.update_settings(gated_teasers=False)
        db.session.commit()
    client = app.test_client()
    login_as(client, member)

    assert b'Crew notes' not in client.get('/blog', base_url=ACME).data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Crew notes' in client.get('/blog', base_url=ACME).data


def test_a_discussion_group_is_gated_by_its_tier(app, acme, user):
    """DiscussionGroup.readable_by_current_visitor, through the page."""
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        DiscussionGroup(name='Crew', slug='crew', org_id=acme.id,
                        description='Where the crew talks',
                        visibility='tier:pro').save()
    member = as_member(app, acme)
    client = app.test_client()
    login_as(client, member)

    page = client.get('/discussions/crew', base_url=ACME)
    assert b'Where the crew talks' not in page.data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Where the crew talks' in client.get('/discussions/crew',
                                                 base_url=ACME).data


def test_a_locked_section_gates_an_item_that_is_public_itself(app, acme,
                                                              user):
    """Content.section_readable_by_current_visitor. The item says public
    and the section says otherwise, and the section wins: that is the rule
    the per-type lock has always had, and a tier is now one of its
    answers."""
    ladder(app, acme, 'Pro')
    gated_article(app, acme, user.id, visibility='public')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('article', visibility='tier:pro')
        db.session.commit()
    member = as_member(app, acme)
    client = app.test_client()
    login_as(client, member)

    assert b'Words.' not in client.get('/blog/crew-notes', base_url=ACME).data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(member.id, acme.id).set_tier(Tier.by_slug('pro'))
    assert b'Words.' in client.get('/blog/crew-notes', base_url=ACME).data


def test_two_communities_ladders_do_not_mix_in_one_app_context(app, acme,
                                                               globex):
    """The memo is keyed by organization. One dict for both would hand a
    globex member acme's ranks, which is the failure the key exists to
    prevent and the one nothing was asserting."""
    ladder(app, acme, 'Supporter', 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert sorted(tier_ranks()) == ['free', 'pro', 'supporter']
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        assert sorted(tier_ranks()) == ['free']


def test_changing_the_ladder_takes_effect_in_the_same_request(app, acme):
    """The console reorders or adds a tier and then renders the page it
    did it on, inside one request. A memo taken before the change answers
    the rest of that request with the ladder it replaced.

    Asserted on the memo rather than through can_read: a move swaps two
    ranks, so the required tier and the member's own shift together and
    the comparison comes out the same either way. What is stale is the
    ladder, so that is what to look at.
    """
    ladder(app, acme, 'Supporter', 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        before = dict(tier_ranks())
        assert before == {'free': 1, 'supporter': 2, 'pro': 3}

        Tier.by_slug('pro').move(-1)
        assert tier_ranks() == {'free': 1, 'pro': 2, 'supporter': 3}

        Tier.add(name='Founder', slug='founder')
        assert tier_ranks()['founder'] == 4

        Tier.by_slug('founder').retire()
        assert 'founder' in tier_ranks()     # retired keeps its rank


def test_a_retired_tier_can_be_brought_back(app, acme):
    ladder(app, acme, 'Pro')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.by_slug('pro').retire()
        assert 'pro' not in [t.slug for t in Tier.in_order()]
        Tier.by_slug('pro').restore()
        assert 'pro' in [t.slug for t in Tier.in_order()]
        assert 'tier:pro' in [value for value, _ in visibility_choices()]


def test_the_bottom_tier_is_found_across_communities(app, acme, globex):
    """The platform console adds a member while resolving no organization
    of its own. Read through the tenant filter the target's tiers are
    invisible, and the caller goes on to create a second Free."""
    with app.app_context():
        assert Tier.bottom(globex.id) is not None
        assert Tier.bottom(globex.id).org_id == globex.id
        assert Tier.bottom(acme.id).org_id == acme.id


# --- writes across communities are refused ------------------------------

def test_a_tier_cannot_be_written_into_another_community(app, acme, globex):
    """The tenant stamp refuses it, which is the guarantee OrgScoped is
    for. Asserted rather than assumed, because the read side leans on it:
    Tier.bottom reads across tenants on purpose, and only the stamp stops
    that becoming a way to write across them too."""
    from app.platform.errors import TenantViolation
    with app.test_request_context(base_url=ACME):
        g.org = acme
        with pytest.raises(TenantViolation):
            Tier(org_id=globex.id, name='Smuggled', slug='smuggled',
                 rank=1).save()


def test_a_membership_cannot_point_at_another_communitys_tier(app, acme,
                                                              globex):
    """Membership is not OrgScoped, deliberately, so the tenant stamp never
    sees this write and nothing else would refuse it. The row would commit
    and then read back with no tier at all, because the loader filter hides
    it, and the member would silently lose every tier-gated thing."""
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        foreign_tier_id = Tier.by_slug('free').id
    member = as_member(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        membership = Membership.get(member.id, acme.id)
        with pytest.raises(ValidationError):
            membership.set_tier(db.session.get(Tier, foreign_tier_id)
                                or Tier(id=foreign_tier_id))


def test_the_first_tier_records_who_created_it(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Tier.by_slug('free').created_by_id == user.id


def test_reordering_records_who_did_it(app, acme, user):
    """A move changes who can read what, so it is the mutation that most
    wants a name against it."""
    ladder(app, acme, 'Pro')
    client = app.test_client()
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from flask_login import login_user
        login_user(user)
        Tier.by_slug('pro').move(-1)
        assert Tier.by_slug('pro').updated_by_id == user.id


def test_the_content_memo_tells_two_tiers_apart(app, acme, user):
    """The per-request memo for latest_content/content_count is keyed by
    the viewer, and the viewer used to be a boolean: member, or not.

    Two members on different tiers now get different rows, so the key has
    to name the tier as well. The situation cannot arise through a real
    request, which serves one viewer, so this reaches the helper through
    the context processor and puts two viewers to it in turn. It is the
    second lock on the same door as _reset_request_state, and the point of
    a second lock is that it holds when the first is forgotten.
    """
    ladder(app, acme, 'Pro')
    free_member = as_member(app, acme, email='free@example.com')
    pro_member = as_member(app, acme, email='pro@example.com', on='pro')
    gated_article(app, acme, user.id, slug='pro-only', title='Pro only')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        # Teasing on, a gated item stays in the list as a locked title for
        # everyone, so the two viewers would see the same rows and the key
        # would prove nothing.
        acme.update_settings(gated_teasers=False)
        db.session.commit()

    with app.test_request_context(base_url=ACME):
        g.org = acme
        context = {}
        for processor in app.template_context_processors[None]:
            context.update(processor())
        latest_content = context['latest_content']

        g.membership = Membership.get(free_member.id, acme.id)
        assert 'Pro only' not in [item.title
                                  for item in latest_content('article')]
        g.membership = Membership.get(pro_member.id, acme.id)
        assert 'Pro only' in [item.title
                              for item in latest_content('article')]
