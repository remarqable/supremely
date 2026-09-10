"""Saying you are coming to an event, and seeing who else is.

One row per person per event, and the row's existence is the answer. The
count is a number so everybody sees it; the faces are member data and
follow the line the member directory already draws.
"""

from datetime import date, timedelta

import pytest
from flask import g

from app.extensions import db
from app.models import Content, Membership, Rsvp, Tier
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def event(app, org, author_id, when=None, slug='crew-call',
          title='Crew call', visibility='public', publish=True):
    """A published event, dated in the future unless told otherwise."""
    when = when or (date.today() + timedelta(days=30)).isoformat()
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type='event', title=title, slug=slug, body='Come.',
                       org_id=org.id, fields={'starts_on': when}, tags=[],
                       visibility=visibility, created_by_id=author_id)
        item.save()
        if publish:
            item.publish()
        return item.id


def member_of(app, org, email='mel@example.com'):
    user = make_user(email=email)
    with app.test_request_context(base_url=ACME):
        g.org = org
        Membership.add(user.id, org.id)
    return user


def going(app, org, content_id) -> int:
    with app.test_request_context(base_url=ACME):
        g.org = org
        return Rsvp.count_for(content_id)


def answer(app, org, user_id, content_id):
    with app.test_request_context(base_url=ACME):
        g.org = org
        Rsvp.toggle(user_id, content_id)


# --- saying you are coming ---------------------------------------------

def test_a_member_says_they_are_coming_and_takes_it_back(app, acme, user):
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)

    client.post(f'/rsvp/{content_id}', base_url=ACME)
    assert going(app, acme, content_id) == 1
    client.post(f'/rsvp/{content_id}', base_url=ACME)
    assert going(app, acme, content_id) == 0


def test_the_database_refuses_a_second_answer_from_one_person(app, acme,
                                                              user):
    """Two presses racing each other both miss the existing row and both
    try to insert. The unique constraint is what keeps one person from
    being counted twice, so it is asserted by attempting it."""
    import sqlalchemy as sa
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Rsvp.toggle(member.id, content_id)
        with pytest.raises(sa.exc.IntegrityError):
            Rsvp(user_id=member.id, content_id=content_id,
                 org_id=acme.id).save()
        db.session.rollback()
        assert Rsvp.count_for(content_id) == 1


def test_losing_that_race_is_not_an_error_page(app, acme, user):
    """Whoever loses it is somebody who pressed a button and is now
    coming, which is what they wanted. It must not be a server error."""
    import app.models.rsvp as rsvp_module
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    answer(app, acme, member.id, content_id)

    client = app.test_client()
    login_as(client, member)
    # Stands in for the race: the row exists, and toggle is told it does
    # not, which is the state both requests are in when they collide.
    original = rsvp_module.Rsvp.toggle.__func__

    def blind_toggle(cls, user_id, content_id):
        cls(user_id=user_id, content_id=content_id).save()
        return True

    rsvp_module.Rsvp.toggle = classmethod(blind_toggle)
    try:
        response = client.post(f'/rsvp/{content_id}', base_url=ACME)
    finally:
        rsvp_module.Rsvp.toggle = classmethod(original)
    assert response.status_code == 302
    assert going(app, acme, content_id) == 1


def test_a_visitor_cannot_answer(app, client, acme, user):
    content_id = event(app, acme, user.id)
    response = client.post(f'/rsvp/{content_id}', base_url=ACME)
    assert response.status_code == 302              # sent to log in
    assert '/auth/login' in response.headers['Location']
    assert going(app, acme, content_id) == 0


def test_you_cannot_answer_for_something_you_cannot_read(app, acme, user):
    """Answering must not be a way to attend something you cannot see.

    A member of the community, gated out of this one event by its tier.
    Somebody who is not a member at all is stopped a step earlier, by the
    permission on the route, so they cannot tell whether this rule holds.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.add(name='Pro', slug='pro')
    content_id = event(app, acme, user.id, slug='pro-only',
                       visibility='tier:pro')
    member = member_of(app, acme)          # on the bottom rung
    client = app.test_client()
    login_as(client, member)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 403
    assert going(app, acme, content_id) == 0


def test_the_refusal_does_not_say_whether_it_has_happened(app, acme, user):
    """The gate is asked before anything about the item is used to
    answer, so a member who may not read it gets the same refusal for a
    future event and a past one. Otherwise the reply is a way to learn
    which."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.add(name='Pro', slug='pro')
    future = event(app, acme, user.id, slug='pro-soon',
                   visibility='tier:pro')
    past = event(app, acme, user.id, when='2020-01-01', slug='pro-past',
                 visibility='tier:pro')
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    codes = {client.post(f'/rsvp/{cid}', base_url=ACME).status_code
             for cid in (future, past)}
    assert codes == {403}


def test_a_member_who_is_not_a_member_here_is_stopped_earlier(app, acme,
                                                              user):
    content_id = event(app, acme, user.id, slug='members-only',
                       visibility='members')
    outsider = make_user(email='outsider@example.com')
    client = app.test_client()
    login_as(client, outsider)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 403
    assert going(app, acme, content_id) == 0


def test_nothing_but_an_attended_type_takes_an_answer(app, acme, user):
    """The type declares it. An article is not something you attend, and
    the route says so rather than storing a row nothing will ever read."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Notes', slug='notes',
                       body='x', org_id=acme.id, fields={}, tags=[],
                       visibility='public', created_by_id=user.id)
        item.save()
        item.publish()
        content_id = item.id
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 404


def test_a_past_event_takes_no_more_answers(app, acme, user):
    content_id = event(app, acme, user.id, when='2020-01-01')
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 404
    assert going(app, acme, content_id) == 0


def test_a_draft_takes_no_answers(app, acme, user):
    """Unpublished content is not there to be answered for, and answering
    must not confirm that it exists."""
    content_id = event(app, acme, user.id, slug='not-yet', publish=False)
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 404
    assert going(app, acme, content_id) == 0


def test_one_community_cannot_answer_for_anothers_event(app, acme, globex):
    content_id = event(app, acme, acme.memberships[0].user_id)
    hank = globex.memberships[0].user
    client = app.test_client()
    login_as(client, hank)
    assert client.post(f'/rsvp/{content_id}',
                       base_url='http://globex.example.test').status_code == 404
    assert going(app, acme, content_id) == 0


# --- what the page shows ------------------------------------------------

def test_the_event_page_offers_the_button(app, acme, user):
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert b"I&#39;m coming!" in page or b"I'm coming!" in page
    assert f'/rsvp/{content_id}'.encode() in page


def test_the_button_says_so_once_you_have_answered(app, acme, user):
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    answer(app, acme, member.id, content_id)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert b"re coming" in page
    # The label shows the state; the accessible name has to say what
    # pressing it does, because that is what gets announced.
    assert b"Say you&#39;re not coming" in page or b"Say you're not coming" in page


def test_an_article_page_offers_nothing(app, client, acme, user):
    """The macro asks the type, so a page that is not an event is
    untouched by any of this."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='article', title='Notes', slug='notes', body='x',
                       org_id=acme.id, fields={}, tags=[],
                       visibility='public', created_by_id=user.id)
        item.save()
        item.publish()
    page = client.get('/blog/notes', base_url=ACME).data
    assert b'/rsvp/' not in page


def test_a_past_event_keeps_its_list_and_loses_its_button(app, acme, user):
    content_id = event(app, acme, user.id, when='2020-01-01')
    member = member_of(app, acme)
    answer(app, acme, member.id, content_id)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert f'/rsvp/{content_id}'.encode() not in page
    assert b'1 came' in page


def test_the_theme_shows_it_too(app, acme, user):
    """An event whose type is set to present through the theme never
    reaches the shell template, so the theme has to carry this as well."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('event', presentation='site')
        db.session.commit()
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/events/crew-call', base_url=ACME)
    assert page.status_code == 200
    assert b'id="rail"' not in page.data          # the theme, not the shell
    assert f'/rsvp/{content_id}'.encode() in page.data


# --- who sees the faces -------------------------------------------------

def coming_member(app, org, name, email):
    person = member_of(app, org, email=email)
    with app.test_request_context(base_url=ACME):
        g.org = org
        from app.models import User
        db.session.get(User, person.id).name = name
        db.session.commit()
    return person


def test_a_member_sees_who_is_coming(app, acme, user):
    content_id = event(app, acme, user.id)
    coming = coming_member(app, acme, 'Ada Lovelace', 'ada@example.com')
    answer(app, acme, coming.id, content_id)
    looker = member_of(app, acme, email='looker@example.com')
    client = app.test_client()
    login_as(client, looker)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert b'Ada Lovelace' in page
    assert b'1 coming' in page


def test_a_visitor_gets_the_count_and_no_faces(app, client, acme, user):
    """Names and avatars are member data. The number is not."""
    content_id = event(app, acme, user.id)
    coming = coming_member(app, acme, 'Ada Lovelace', 'ada@example.com')
    answer(app, acme, coming.id, content_id)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert b'1 coming' in page
    assert b'Ada Lovelace' not in page


def test_a_name_is_readable_without_a_mouse(app, acme, user):
    """An avatar is hidden from assistive tech, so a face with the name
    only in a tooltip reads as nothing at all."""
    content_id = event(app, acme, user.id)
    coming = coming_member(app, acme, 'Ada Lovelace', 'ada@example.com')
    answer(app, acme, coming.id, content_id)
    looker = member_of(app, acme, email='looker@example.com')
    client = app.test_client()
    login_as(client, looker)
    page = client.get('/events/crew-call', base_url=ACME).data
    # The name itself, in a span a screen reader reads. `sr-only` alone
    # would pass on the skip link at the top of every page.
    assert b'<span class="sr-only">Ada Lovelace</span>' in page


def test_deleting_an_event_takes_its_guest_list(app, acme, user):
    """A foreign key rather than a loose id: the alternative leaves rows
    pointing at nothing, and nothing to catch them."""
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    answer(app, acme, member.id, content_id)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        db.session.get(Content, content_id).delete()
        db.session.commit()
        assert Rsvp.query.filter_by(content_id=content_id).count() == 0


# --- an undated or oddly dated item -------------------------------------

def test_a_value_that_is_not_a_date_does_not_break_the_page(app, acme,
                                                            user):
    """`fields` is a JSON blob, and its values are whatever a seeder, an
    importer or a plugin wrote."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = Content(type='event', title='Odd', slug='odd', body='x',
                       org_id=acme.id, fields={'starts_on': 20260101},
                       tags=[], visibility='public', created_by_id=user.id)
        item.save()
        item.publish()
        assert item.has_happened() is False
        assert item.is_upcoming() is False
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.get('/events/odd', base_url=ACME).status_code == 200


# --- the day itself -----------------------------------------------------

def test_an_event_today_has_not_happened_yet(app, acme, user):
    """The boundary between the two rules, and the only thing separating
    them. An event happening this evening still takes answers and still
    shows in the rail."""
    today = date.today().isoformat()
    content_id = event(app, acme, user.id, when=today)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = db.session.get(Content, content_id)
        assert item.has_happened() is False
        assert item.is_upcoming() is True

    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 302
    assert going(app, acme, content_id) == 1


def test_yesterday_has_happened(app, acme, user):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    content_id = event(app, acme, user.id, when=yesterday, slug='yesterday')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        item = db.session.get(Content, content_id)
        assert item.has_happened() is True
        assert item.is_upcoming() is False


def test_two_events_on_one_day_do_not_break_the_rail(app, acme, user):
    """Picking the next event compared the pairs, so a tie on the date
    fell through to comparing two rows, which raises."""
    when = (date.today() + timedelta(days=5)).isoformat()
    event(app, acme, user.id, when=when, slug='one', title='One')
    event(app, acme, user.id, when=when, slug='two', title='Two')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Content.upcoming_event() is not None


# --- a type the community has switched off ------------------------------

def test_a_switched_off_type_takes_no_answers(app, acme, user):
    """The read path refuses a type the organization has turned off, and
    answering has to refuse it the same way: a different reply is a way
    to learn that hidden content is there."""
    content_id = event(app, acme, user.id)
    member = member_of(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('event', enabled=False)
        db.session.commit()
    client = app.test_client()
    login_as(client, member)
    assert client.get('/events/crew-call', base_url=ACME).status_code == 404
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 404
    assert going(app, acme, content_id) == 0


def test_with_teasing_off_a_refusal_says_nothing_at_all(app, acme, user):
    """Teasing off means the gate degrades to hiding. Answering must
    degrade with it, or it becomes the way to enumerate what was hidden.
    """
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Tier.add(name='Pro', slug='pro')
        acme.update_settings(gated_teasers=False)
        db.session.commit()
    content_id = event(app, acme, user.id, slug='pro-only',
                       visibility='tier:pro')
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    assert client.get('/events/pro-only', base_url=ACME).status_code == 404
    assert client.post(f'/rsvp/{content_id}', base_url=ACME).status_code == 404


def test_a_past_event_nobody_came_to_draws_nothing(app, acme, user):
    content_id = event(app, acme, user.id, when='2020-01-01')
    member = member_of(app, acme)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/events/crew-call', base_url=ACME).data
    assert f'/rsvp/{content_id}'.encode() not in page
    assert b'came' not in page
    # Not merely empty of words: the bordered strip itself must not be
    # drawn, or the page carries a bare band with 20px of nothing in it.
    band = b'mt-8 flex flex-wrap items-center gap-3 border-t'
    assert page.count(band) == 0
