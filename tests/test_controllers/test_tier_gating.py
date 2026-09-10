"""Stage 3: choosing a tier, and not losing one by accident.

Stage 1 widened what a visibility column may hold. This is the stage that
widens the forms which write it. Until it landed, every one of them failed
in the same direction: an item gated to a tier came back public.
"""

from flask import g

from app.extensions import db
from app.models import Content, DiscussionGroup, Tier
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def add_tier(app, org, name='Pro', slug='pro'):
    with app.test_request_context(base_url=ACME):
        g.org = org
        return Tier.add(name=name, slug=slug).id


def article(app, org, author_id, visibility='tier:pro', slug='crew-notes'):
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type='article', title='Crew notes', slug=slug,
                       body='Words.', org_id=org.id, fields={}, tags=[],
                       visibility=visibility, created_by_id=author_id)
        item.save()
        item.publish()
        return item.id


def visibility_of(app, org, content_id):
    with app.test_request_context(base_url=ACME):
        g.org = org
        return db.session.get(Content, content_id).visibility


# --- the pickers offer tiers -------------------------------------------

def test_the_editor_offers_every_active_tier(app, client, acme, user):
    add_tier(app, acme)
    login_as(client, user)
    page = client.get('/manage/content/article/new', base_url=ACME)
    assert b'value="tier:pro"' in page.data
    assert b'>Pro<' in page.data


def test_the_group_form_and_the_area_switch_offer_them_too(app, client, acme,
                                                           user):
    add_tier(app, acme)
    login_as(client, user)
    page = client.get('/manage/discussions', base_url=ACME).data
    assert page.count(b'value="tier:pro"') >= 2   # the switch and the form


def test_the_type_lock_offers_them(app, client, acme, user):
    add_tier(app, acme)
    login_as(client, user)
    page = client.get('/manage/content-types', base_url=ACME)
    assert b'value="tier:pro"' in page.data


# --- and none of the writers loses one ---------------------------------

def test_saving_a_title_does_not_unlock_a_gated_article(app, client, acme,
                                                        user):
    """The editor's select could not represent a tier, so it came up with
    nothing chosen, the browser posted the first option, and saving
    anything at all turned a gated item public."""
    add_tier(app, acme)
    content_id = article(app, acme, user.id)
    login_as(client, user)
    page = client.get(f'/manage/content/{content_id}/edit', base_url=ACME)
    assert b'value="tier:pro" selected' in page.data

    client.post(f'/manage/content/{content_id}/edit', base_url=ACME,
                data={'title': 'Crew notes, edited', 'slug': 'crew-notes',
                      'body': 'Words.', 'visibility': 'tier:pro'})
    assert visibility_of(app, acme, content_id) == 'tier:pro'


def test_a_form_that_cannot_name_the_tier_leaves_it_alone(app, client, acme,
                                                          user):
    """A post with no visibility field at all, which is what an older form
    or a crafted request looks like. Falling back to public is the one
    answer that must never happen."""
    add_tier(app, acme)
    content_id = article(app, acme, user.id)
    login_as(client, user)
    client.post(f'/manage/content/{content_id}/edit', base_url=ACME,
                data={'title': 'Crew notes', 'slug': 'crew-notes',
                      'body': 'Words.'})
    assert visibility_of(app, acme, content_id) == 'tier:pro'


def test_the_type_lock_keeps_a_tier_across_an_unrelated_save(app, client,
                                                             acme, user):
    add_tier(app, acme)
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('article', visibility='tier:pro')
        db.session.commit()
    # Posted with the field present, which is what the form does: the
    # writer used to keep only the two base levels and clamp anything else
    # to None, and None reads as public.
    client.post('/manage/content-types/article', base_url=ACME,
                data={'enabled': 'on', 'tease': 'yes',
                      'visibility': 'tier:pro'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert acme.type_visibility('article') == 'tier:pro'


def test_a_tier_gated_group_can_be_un_gated_again(app, client, acme, user):
    """The row carried a two-state toggle, which could neither reach a
    tier nor come back from one: a group gated to a tier had no way out
    short of deleting it. It carries the same picker as everything else
    now, and the list row shows what the group is actually set to."""
    add_tier(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group_id = DiscussionGroup(name='Crew', slug='crew', org_id=acme.id,
                                   visibility='tier:pro').save().id
    login_as(client, user)
    page = client.get('/manage/discussions', base_url=ACME).data
    # Anchored on this group's own form: the page carries another select
    # listing group names, which a split on the name would land in.
    marker = f'/manage/discussions/{group_id}/visibility'.encode()
    row = page.split(marker)[1].split(b'</form>')[0]
    assert b'value="tier:pro" selected' in row

    client.post(f'/manage/discussions/{group_id}/visibility', base_url=ACME,
                data={'visibility': 'members'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert db.session.get(DiscussionGroup, group_id).visibility == 'members'


def test_a_group_can_be_moved_onto_a_tier_from_the_list(app, client, acme,
                                                        user):
    add_tier(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group_id = DiscussionGroup(name='Crew', slug='crew', org_id=acme.id,
                                   visibility='members').save().id
    login_as(client, user)
    client.post(f'/manage/discussions/{group_id}/visibility', base_url=ACME,
                data={'visibility': 'tier:pro'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert db.session.get(DiscussionGroup,
                              group_id).visibility == 'tier:pro'


def test_the_area_switch_accepts_a_tier(app, client, acme, user):
    add_tier(app, acme)
    login_as(client, user)
    client.post('/manage/discussions', base_url=ACME,
                data={'area_visibility': 'tier:pro'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert DiscussionGroup.area_visibility() == 'tier:pro'


def test_a_group_can_be_created_on_a_tier(app, client, acme, user):
    add_tier(app, acme)
    login_as(client, user)
    client.post('/manage/discussions', base_url=ACME,
                data={'name': 'Crew', 'slug': 'crew',
                      'visibility': 'tier:pro'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert DiscussionGroup.get_by_slug('crew').visibility == 'tier:pro'


def test_creating_a_group_on_a_value_nobody_knows_is_reported(app, client,
                                                              acme, user):
    """Nothing is stored yet, so there is nothing to fall back to. The
    value is refused and said so, rather than quietly becoming something
    else."""
    login_as(client, user)
    page = client.post('/manage/discussions', base_url=ACME,
                       data={'name': 'Crew', 'slug': 'crew',
                             'visibility': 'tier:nonesuch'},
                       follow_redirects=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert DiscussionGroup.get_by_slug('crew') is None
    assert b'Invalid visibility' in page.data


# --- what the labels say ------------------------------------------------

def test_a_tier_reads_as_its_name_not_a_missing_key(app, client, acme, user):
    """Templates built the catalogue key by hand, so a tier value printed
    the raw key `manage.visibility_tier:pro` on the page."""
    add_tier(app, acme)
    article(app, acme, user.id)
    login_as(client, user)
    page = client.get('/manage/content/article', base_url=ACME).data
    assert b'manage.visibility_tier' not in page
    # The badge on the row, not the word anywhere on the page: the manage
    # shell contains "Pro" of its own accord.
    row = page.split(b'Crew notes')[1].split(b'</tr>')[0]
    assert b'>Pro<' in row


# --- the gate names the tier -------------------------------------------

def test_the_gate_says_which_tier_would_open_it(app, acme, user):
    add_tier(app, acme)
    article(app, acme, user.id)
    member = make_user(email='mel@example.com')
    from app.models import Membership
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.add(member.id, acme.id)
    client = app.test_client()
    login_as(client, member)
    page = client.get('/blog/crew-notes', base_url=ACME)
    assert page.status_code == 200
    assert b'This is for Pro members.' in page.data
    assert b'Words.' not in page.data


def test_a_members_only_gate_is_unchanged(app, client, acme, user):
    """Nothing about the ordinary members-only gate moves."""
    article(app, acme, user.id, visibility='members', slug='members-only')
    page = client.get('/blog/members-only', base_url=ACME)
    assert page.status_code == 200
    assert b'Members only' in page.data
    assert b'This is for' not in page.data


def test_the_editor_keeps_a_retired_tier_selected(app, client, acme, user):
    """The picker offers active tiers only, so an item gated to a retired
    one would render with nothing chosen. The browser then posts the first
    option and the next save makes it public, which is exactly what the
    retained option exists to prevent."""
    tier_id = add_tier(app, acme)
    content_id = article(app, acme, user.id)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        db.session.get(Tier, tier_id).retire()
    login_as(client, user)
    page = client.get(f'/manage/content/{content_id}/edit', base_url=ACME)
    assert b'value="tier:pro" selected' in page.data


def test_creating_content_on_a_value_nobody_knows_is_reported(app, client,
                                                              acme, user):
    """Nothing is stored yet, so there is nothing to fall back to. The old
    fallback landed on public, which is the answer this whole stage exists
    to prevent."""
    login_as(client, user)
    page = client.post('/manage/content/article/new', base_url=ACME,
                       data={'title': 'Smuggled', 'slug': 'smuggled',
                             'body': 'x', 'visibility': 'tier:nonesuch'},
                       follow_redirects=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Content.published_by_slug('article', 'smuggled') is None
    assert b'Invalid visibility' in page.data


def test_a_child_page_names_the_tier_its_parent_needs(app, acme, user):
    """The nested route gates too, and gained the tier last of all."""
    add_tier(app, acme)
    from app.models import Membership
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('course', enabled=True)
        acme.set_type_settings('lesson', enabled=True)
        db.session.commit()
        parent = Content(type='course', title='Navigation', slug='navigation',
                         body='x', org_id=acme.id, fields={}, tags=[],
                         visibility='tier:pro', created_by_id=user.id)
        parent.save()
        parent.publish()
        member = make_user(email='mel@example.com')
        Membership.add(member.id, acme.id)
    client = app.test_client()
    login_as(client, member)
    # The parent's gate runs before the child is even looked up, so the
    # lesson need not exist for this to be the right answer.
    page = client.get('/courses/navigation/lessons/anything', base_url=ACME)
    assert page.status_code == 200
    assert b'This is for Pro members.' in page.data


def test_the_ladder_is_read_once_for_a_whole_page(app, client, acme, user):
    """The label helper runs once per option in every picker on the page.
    Read from the database each time, one content-types page was sixty-odd
    identical selects against a table with three rows in it."""
    add_tier(app, acme)
    add_tier(app, acme, name='Supporter', slug='supporter')
    statements = []
    from sqlalchemy import event as sa_event
    engine = db.engine

    def record(conn, cursor, statement, params, context, many):
        if 'FROM tier' in statement:
            statements.append(statement)

    login_as(client, user)
    sa_event.listen(engine, 'before_cursor_execute', record)
    try:
        page = client.get('/manage/content-types', base_url=ACME)
    finally:
        sa_event.remove(engine, 'before_cursor_execute', record)
    assert page.status_code == 200
    assert len(statements) <= 2, f'{len(statements)} tier queries for one page'
