"""Content Types: a developer defines a new vertical without touching the
Content subsystem."""

import pytest
from flask import g

from app.models import Content
from app.platform import content_types as ct_module
from app.platform.content_types import (
    ContentType,
    FieldSpec,
    active_types,
    register_content_type,
    type_for_base,
)
from app.platform.errors import ValidationError
from tests.conftest import enable_types, login_as

ACME = 'http://acme.example.test'


@pytest.fixture
def cocktail_type():
    """A vertical the application has never heard of, registered on the fly.

    Deliberately not one of the shipped slugs: the claim is that a type
    nobody wrote into the publishing subsystem still routes and renders.
    """
    cocktail = register_content_type(ContentType(
        slug='cocktail', singular='Cocktail', plural='Cocktails',
        base='/cocktails', show_in_nav=True,
        fields=(
            FieldSpec(key='prep_minutes', type='number', label='Prep time',
                      required=True),
            FieldSpec(key='vegetarian', type='boolean', label='Vegetarian'),
        )))
    yield cocktail
    ct_module.CONTENT_TYPES.pop('cocktail', None)


def test_core_types_registered(app):
    assert {'page', 'article', 'event'} <= set(
        ct_module.CONTENT_TYPES)
    assert ct_module.CONTENT_TYPES['page'].is_page
    assert not ct_module.CONTENT_TYPES['article'].is_page
    assert type_for_base('/blog').slug == 'article'


def test_custom_type_end_to_end(app, client, acme, globex, cocktail_type):
    # A type nobody has heard of is off until this organization asks for it,
    # the same as any other.
    enable_types(acme, 'cocktail')
    with app.test_request_context():
        g.org = acme
        c = Content(type='cocktail', title='Garlic Soup', slug='garlic-soup',
                    body='Boil the garlic.', org_id=acme.id)
        c.set_structured_fields({'prep_minutes': '25', 'vegetarian': 'on'})
        c.save()
        c.publish()
    assert c.fields == {'prep_minutes': 25, 'vegetarian': True}

    # New base routes automatically: archive + single, no core change.
    archive = client.get('/cocktails', base_url='http://acme.example.test')
    assert archive.status_code == 200
    assert b'Garlic Soup' in archive.data
    single = client.get('/cocktails/garlic-soup',
                        base_url='http://acme.example.test')
    assert single.status_code == 200
    assert b'Boil the garlic.' in single.data


def test_definitions_validated():
    with pytest.raises(ValueError, match='needs a URL base'):
        ContentType(slug='x', singular='X', plural='Xs').validate_definition()
    with pytest.raises(ValueError, match='singular and plural'):
        ContentType(slug='x', singular='', plural='', kind='page'
                    ).validate_definition()
    with pytest.raises(ValueError, match='kind'):
        ContentType(slug='x', singular='X', plural='Xs', kind='pamphlet'
                    ).validate_definition()
    with pytest.raises(ValueError, match='no URL base'):
        ContentType(slug='x', singular='X', plural='Xs', kind='page',
                    base='/x').validate_definition()


def test_a_type_is_a_page_or_a_post_and_says_which():
    """has_archive and is_page are read off `kind` rather than declared
    beside it, so the two cannot come to disagree about the same type."""
    page = ContentType(slug='p', singular='P', plural='Ps', kind='page')
    post = ContentType(slug='q', singular='Q', plural='Qs', base='/qs')
    assert page.is_page and not page.has_archive
    assert post.has_archive and not post.is_page
    assert post.kind == 'post'                  # the default: most types are


def test_base_collision_rejected(cocktail_type):
    with pytest.raises(ValueError, match='base'):
        register_content_type(ContentType(
            slug='recipe2', singular='R2', plural='R2s', base='/cocktails'))


def test_duplicate_slug_rejected(cocktail_type):
    with pytest.raises(ValueError, match='already registered'):
        register_content_type(ContentType(
            slug='recipe', singular='Again', plural='Agains', base='/r2'))

# --- the widened field vocabulary ---------------------------------------------

def spec_of(**kwargs):
    kwargs.setdefault('key', 'f')
    kwargs.setdefault('label', 'Field')
    return FieldSpec(**kwargs)


def test_a_select_stores_the_key_and_refuses_anything_else():
    """The key is stored, not the label, so rewording a label later does not
    orphan every row that chose it."""
    spec = spec_of(type='select', choices=(('full_time', 'Full time'),
                                           ('contract', 'Contract')))
    assert spec.clean('contract') == 'contract'
    assert spec.clean('') is None
    for refused in ('Contract', 'freelance', 'full time'):
        with pytest.raises(ValidationError):
            spec.clean(refused)


def test_a_datetime_is_stored_in_utc():
    spec = spec_of(type='datetime')
    # A browser's datetime-local sends no zone; read as UTC rather than
    # guessed at.
    assert spec.clean('2026-05-24T14:30') == '2026-05-24T14:30:00+00:00'
    # An offset that is given is honoured and normalised.
    assert spec.clean('2026-05-24T14:30:00+02:00') == '2026-05-24T12:30:00+00:00'
    with pytest.raises(ValidationError):
        spec.clean('next tuesday')


def test_an_upload_field_refuses_an_id_that_is_not_ours(app, acme, globex):
    """The id is resolved through a query rather than trusted, so typing
    another organization's upload id into the form stores nothing."""
    from flask import g

    from app.models import Upload
    from tests.conftest import make_png
    spec = spec_of(type='image')
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        theirs = Upload.from_file(_png_storage(make_png(), 'theirs.png'))
        theirs_id = theirs.id

    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        ours = Upload.from_file(_png_storage(make_png(), 'ours.png'))
        assert spec.clean(str(ours.id)) == ours.id
        with pytest.raises(ValidationError):
            spec.clean(str(theirs_id))
        with pytest.raises(ValidationError):
            spec.clean('not-a-number')


def _png_storage(data, filename):
    import io as _io

    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=_io.BytesIO(data), filename=filename,
                       content_type='image/png')


def test_a_list_cleans_each_row_and_drops_the_empty_ones():
    spec = spec_of(type='list', of=(
        FieldSpec(key='amount', type='string', label='Amount'),
        FieldSpec(key='item', type='string', label='Item', required=True)))
    rows = spec.clean([
        {'amount': '2', 'item': 'onions'},
        {'amount': '', 'item': ''},          # the blank row an editor offers
        {'amount': '1 tsp', 'item': 'salt'},
    ])
    assert rows == [{'amount': '2', 'item': 'onions'},
                    {'amount': '1 tsp', 'item': 'salt'}]
    assert spec.clean([]) is None
    assert spec.clean([{'amount': '', 'item': ''}]) is None


def test_a_row_is_held_to_its_sub_fields_rules():
    spec = spec_of(type='list', of=(
        FieldSpec(key='item', type='string', label='Item', required=True),
        FieldSpec(key='link', type='url', label='Link')))
    with pytest.raises(ValidationError):
        spec.clean([{'item': 'onions', 'link': 'not-a-url'}])
    with pytest.raises(ValidationError):
        spec.clean([{'item': '', 'link': 'https://example.com'}])


def test_a_list_is_capped():
    """A repeating field holds rows, not a spreadsheet."""
    from app.platform.content_types import LIST_ROW_CAP
    spec = spec_of(type='list', of=(FieldSpec(key='item', type='string'),))
    ok = [{'item': f'row {n}'} for n in range(LIST_ROW_CAP)]
    assert len(spec.clean(ok)) == LIST_ROW_CAP
    with pytest.raises(ValidationError):
        spec.clean([*ok, {'item': 'one too many'}])


def test_a_list_cannot_contain_a_list():
    """One level. A row of rows needs an editor that nests, and the value of
    a repeating field is that it stays a table somebody can read."""
    with pytest.raises(ValueError, match='cannot contain a list'):
        ContentType(slug='nested', singular='N', plural='Ns', base='/ns',
                    fields=(FieldSpec(key='outer', type='list', of=(
                        FieldSpec(key='inner', type='list', of=(
                            FieldSpec(key='x', type='string'),)),)),)
                    ).validate_definition()


def test_a_field_type_must_declare_what_it_needs():
    with pytest.raises(ValueError, match='needs choices'):
        ContentType(slug='a', singular='A', plural='As', base='/as',
                    fields=(FieldSpec(key='s', type='select'),)
                    ).validate_definition()
    with pytest.raises(ValueError, match='needs `of`'):
        ContentType(slug='b', singular='B', plural='Bs', base='/bs',
                    fields=(FieldSpec(key='l', type='list'),)
                    ).validate_definition()
    with pytest.raises(ValueError, match='only a list takes'):
        ContentType(slug='c', singular='C', plural='Cs', base='/cs',
                    fields=(FieldSpec(key='s', type='string',
                                      of=(FieldSpec(key='x'),)),)
                    ).validate_definition()


def test_a_row_is_read_from_its_own_index_not_its_position(app):
    """Names carry the row index, so a missing value leaves a gap in that
    row rather than pulling the next row's values up into it.

    Gaps in the numbering and rows posted out of order are both fine: the
    index orders the rows and the stored list is renumbered from zero.
    """
    from werkzeug.datastructures import MultiDict

    from app.platform.content_types import submitted_fields
    content_type = ContentType(
        slug='z', singular='Z', plural='Zs', base='/zs',
        fields=(FieldSpec(key='servings', type='number', label='Servings'),
                FieldSpec(key='ingredients', type='list', label='Ingredients',
                          of=(FieldSpec(key='amount', type='string'),
                              FieldSpec(key='item', type='string')))))
    form = MultiDict([
        ('field_servings', '4'),
        ('field_ingredients__2__item', 'salt'),          # out of order
        ('field_ingredients__2__amount', '1 tsp'),
        ('field_ingredients__0__amount', '2'),
        ('field_ingredients__0__item', 'onions'),
        ('field_ingredients__1__item', 'garlic'),        # no amount posted
    ])
    assert submitted_fields(content_type, form) == {
        'servings': '4',
        'ingredients': [{'amount': '2', 'item': 'onions'},
                        {'item': 'garlic'},
                        {'amount': '1 tsp', 'item': 'salt'}],
    }


def test_more_rows_than_the_cap_are_not_all_built(app):
    """The cap refuses the save, but it should not cost the work of
    materialising every row somebody chose to post first."""
    from werkzeug.datastructures import MultiDict

    from app.platform.content_types import LIST_ROW_CAP, submitted_fields
    content_type = ContentType(
        slug='y', singular='Y', plural='Ys', base='/ys',
        fields=(FieldSpec(key='items', type='list', label='Items',
                          of=(FieldSpec(key='item', type='string'),)),))
    form = MultiDict([(f'field_items__{n}__item', f'row {n}')
                      for n in range(2000)])
    built = submitted_fields(content_type, form)['items']
    assert len(built) == LIST_ROW_CAP + 1
    with pytest.raises(ValidationError, match='at most'):
        content_type.fields[0].clean(built)


# --- what an organization publishes -------------------------------------------

def test_the_library_is_a_shelf_not_a_delivery(app, client, acme, globex, user):
    """A new community gets what its starter content uses and nothing else.

    The point of a library is choosing from it. Before this every
    organization was handed a podcast archive, a jobs board and a recipe
    section whether it wanted them or not.
    """
    from app.platform.content_types import CONTENT_TYPES
    with app.test_request_context(base_url=ACME):
        g.org = acme
        active = set(active_types())
    assert {'page', 'article', 'event'} <= active          # core
    # Everything Supremely shipped before types became a choice.
    assert {'episode', 'recording', 'resource', 'announcement',
            'team_member'} <= active
    # Everything added with the library, which is asked for rather than given.
    assert not ({'recipe', 'job', 'opportunity', 'gallery'} & active)
    assert set(CONTENT_TYPES) - active                     # something is off


def test_nothing_that_worked_before_stops_working(app, client, acme, globex,
                                                  user):
    """Turning the library into a shelf must not take away what was already
    on it. Every type Supremely shipped before types became a choice is
    still on for a new community, and its address still answers.

    Only what was added alongside this stage starts off, which is the whole
    point: a jobs board is something to ask for.
    """
    was_always_on = {'/blog': 'article', '/events': 'event',
                     '/announcements': 'announcement',
                     '/recordings': 'recording', '/podcast': 'episode',
                     '/resources': 'resource', '/team': 'team_member'}
    for path, slug in was_always_on.items():
        assert client.get(path, base_url=ACME).status_code == 200, slug

    # Added with the library, so off until somebody asks.
    for path in ('/recipes', '/jobs', '/opportunities', '/galleries'):
        assert client.get(path, base_url=ACME).status_code == 404, path


def test_page_and_article_cannot_be_turned_off(app, acme):
    """Without them there is nothing to publish at all, so they are not a
    choice an organization gets to make."""
    from app.platform.content_types import CONTENT_TYPES
    acme.set_type_settings('page', enabled=False)
    acme.set_type_settings('article', enabled=False)
    assert acme.type_enabled(CONTENT_TYPES['page'])
    assert acme.type_enabled(CONTENT_TYPES['article'])


def test_turning_a_type_off_hides_it_and_keeps_it(app, client, acme, globex,
                                                  user):
    """The stage's acceptance, end to end. Disabling is not deleting: the
    rows wait, and turning it back on restores exactly what was there."""
    enable_types(acme, 'episode')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        episode = Content(type='episode', title='Episode one',
                          slug='episode-one', body='Notes.', org_id=acme.id,
                          fields={'audio_url': 'https://cdn.example.com/1.mp3'},
                          tags=[])
        episode.save()
        episode.publish()

    assert client.get('/podcast', base_url=ACME).status_code == 200
    login_as(client, user)
    assert b'Podcast' in client.get('/dashboard', base_url=ACME).data

    client.post('/manage/content-types/episode', base_url=ACME,
                data={'visibility': 'public', 'tease': 'inherit'})

    assert client.get('/podcast', base_url=ACME).status_code == 404
    assert client.get('/podcast/episode-one', base_url=ACME).status_code == 404
    assert b'Podcast' not in client.get('/dashboard', base_url=ACME).data
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Content.query.filter_by(slug='episode-one').first() is not None

    client.post('/manage/content-types/episode', base_url=ACME,
                data={'enabled': 'on', 'visibility': 'public',
                      'tease': 'inherit'})
    assert client.get('/podcast/episode-one', base_url=ACME).status_code == 200


def test_two_organizations_keep_their_own_type_sets(app, client, acme, globex,
                                                    user):
    """A tenancy claim, so it is proven rather than assumed: what one
    organization publishes says nothing about what another does."""
    enable_types(acme, 'recipe')
    globex_org = globex
    enable_types(globex_org, 'job')

    # Asked about one organization while another is the tenant in force.
    # Setting g.org to whichever org is being asked about proves nothing:
    # a settings read that went to g.org rather than to the instance would
    # pass every time.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert globex_org.type_settings('job').get('enabled') is True
        assert globex_org.type_settings('recipe') == {}
        assert acme.type_settings('recipe').get('enabled') is True
        assert acme.type_settings('job') == {}
        assert 'recipe' in active_types()
        assert 'job' not in active_types()
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex_org
        assert 'job' in active_types()
        assert 'recipe' not in active_types()

    assert client.get('/recipes', base_url=ACME).status_code == 200
    assert client.get('/jobs', base_url=ACME).status_code == 404
    assert client.get('/jobs',
                      base_url='http://globex.example.test').status_code == 200
    assert client.get('/recipes',
                      base_url='http://globex.example.test').status_code == 404


def test_who_may_read_and_how_a_refusal_looks_are_separate(app, acme):
    """The design tiers will inherit. Folding them into one three-valued
    setting leaves nowhere for a tier to be added on the who axis, because
    "teaser" is not a kind of reader."""
    from app.platform.authz import VISIBILITY_LEVELS
    acme.set_type_settings('article', visibility='members', tease=False)
    assert acme.type_visibility('article') == 'members'
    assert acme.type_teases('article') is False
    assert acme.type_visibility('article') in VISIBILITY_LEVELS

    # Inherit is a real state, not a value pinned on save.
    acme.set_type_settings('article', tease=None)
    acme.update_settings(gated_teasers=True)
    assert acme.type_teases('article') is True
    acme.update_settings(gated_teasers=False)
    assert acme.type_teases('article') is False


def test_a_type_outside_a_request_answers_as_it_always_did(app):
    """CLI, jobs and workers have no tenant. A plugin's types fail closed
    there, and a registered core type stays active, which is what seeds and
    the command line rely on."""
    from app.platform.content_types import CONTENT_TYPES, type_is_active
    with app.app_context():
        assert type_is_active(CONTENT_TYPES['article'])
        assert not type_is_active(CONTENT_TYPES['definition'])   # a plugin's


def test_an_organization_can_move_a_type_to_the_other_surface(app, client,
                                                              acme, globex,
                                                              user):
    """A type says where it renders and an organization may disagree.

    Team is site furniture for most communities, a roster on the website.
    For one that runs its directory behind the door, it belongs in the
    shell. Absent means the type's own answer, so this is an override
    rather than a copy of today's default.
    """
    from app.platform.content_types import (
        CONTENT_TYPES,
        community_types,
        type_presentation,
    )
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert type_presentation(CONTENT_TYPES['team_member']) == 'site'

    login_as(client, user)
    themed = client.get('/team', base_url=ACME).get_data(as_text=True)

    client.post('/manage/content-types/team_member', base_url=ACME,
                data={'enabled': 'on', 'visibility': 'public',
                      'tease': 'inherit', 'presentation': 'community'})

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert type_presentation(CONTENT_TYPES['team_member']) == 'community'
        # And the sidebar follows, because it asks the same question.
        assert 'team_member' in {c.slug for c in community_types()}

    # The page itself changes surface. Asserting only the resolver leaves
    # the render seam untested: reverting site.py to the type's own answer
    # kept every test green. Asserted on the shell's own sidebar rather than
    # on the two responses differing, which a flash message alone would do.
    shell = client.get('/team', base_url=ACME).get_data(as_text=True)
    # The rail is furniture only the shell has. Asserting that the two
    # responses merely differ is not enough: a flash message from the save
    # does that on its own.
    assert 'Upcoming Event' not in themed
    assert 'Upcoming Event' in shell

    # Back to inherit: the stored answer goes rather than being pinned.
    client.post('/manage/content-types/team_member', base_url=ACME,
                data={'enabled': 'on', 'visibility': 'public',
                      'tease': 'inherit', 'presentation': ''})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert 'presentation' not in acme.type_settings('team_member')
        assert type_presentation(CONTENT_TYPES['team_member']) == 'site'


def test_a_partial_save_does_not_reset_the_rest(app, client, acme, globex,
                                                user):
    """An absent field is not a request to clear it.

    Storing every absent value as None would read as "no opinion", and a
    save that only meant to turn something on would quietly unlock a locked
    section on the way past.
    """
    login_as(client, user)
    client.post('/manage/content-types/article', base_url=ACME,
                data={'enabled': 'on', 'visibility': 'members',
                      'tease': 'no'})
    assert acme.type_visibility('article') == 'members'
    assert acme.type_teases('article') is False

    client.post('/manage/content-types/article', base_url=ACME,
                data={'enabled': 'on'})          # nothing else posted
    assert acme.type_visibility('article') == 'members'
    assert acme.type_teases('article') is False


def test_a_plugins_types_are_not_switched_here(app, client, acme, globex,
                                               user):
    """Installing the plugin is the act of choosing them, so an uninstalled
    plugin's type has no row to draw and no switch that would do
    anything."""
    login_as(client, user)
    page = client.get('/manage/content-types', base_url=ACME).get_data(
        as_text=True)
    # The row's own form, not the word: Glossary also names a plugin in
    # the Manage sidebar, which is a different thing entirely.
    assert '/manage/content-types/definition' not in page
    assert client.post('/manage/content-types/definition',
                       base_url=ACME, data={'enabled': 'on'}).status_code == 404
    assert acme.type_settings('definition') == {}


def test_an_archive_reads_in_the_order_its_type_declares(app, acme):
    """Posts are not all chronological. A roster reads by name, a glossary
    A to Z, and lessons in the order somebody put them in.

    Ordered in the query, never in a template: a theme sorting an archive
    back into shape is a theme working around the model, and themes are
    renderers.
    """
    from flask import g

    from app.models import Content
    with app.test_request_context():
        g.org = acme
        # Chosen so that comparing bytes and comparing letters disagree.
        # SQLite compares bytes, which puts every capital before every
        # lowercase letter: 'Banana', 'apple', 'cherry'. PostgreSQL compares
        # by locale and gives 'apple', 'Banana', 'cherry'. Titles that were
        # all capitalised would sort the same either way, and would have let
        # one engine's answer stand in for the rule.
        for slug, title in (('cherry', 'cherry'), ('banana', 'Banana'),
                            ('apple', 'apple')):
            member = Content(type='team_member', title=title, slug=slug,
                             body='', org_id=acme.id, fields={}, tags=[],
                             visibility='public')
            member.save()
            member.publish()
        titles = [item.title for item
                  in Content.published_query('team_member').all()]
    assert titles == ['apple', 'Banana', 'cherry'], titles


def test_the_default_is_still_newest_first(app, acme):
    """Changing what a roster does must not change what a blog does."""
    from flask import g

    from app.models import Content
    with app.test_request_context():
        g.org = acme
        for slug in ('one', 'two', 'three'):
            item = Content(type='article', title=slug.title(), slug=slug,
                           body='', org_id=acme.id, fields={}, tags=[],
                           visibility='public')
            item.save()
            item.publish()
        newest_first = Content.published_query('article').all()
    dates = [item.published_at for item in newest_first]
    assert dates == sorted(dates, reverse=True)


def test_manual_orders_by_position_and_puts_the_unarranged_last(app, acme):
    """The ordering clause itself.

    Nothing in the library declares 'manual', because position is where a
    block sits inside its parent and every listing is of standalone rows.
    The clause is still asserted, because the value is offered and the next
    type to want it should find it working.
    """
    from flask import g

    from app.models import Content
    from app.platform.content_types import get_content_type
    with app.test_request_context():
        g.org = acme
        clause = Content.order_for(get_content_type('team_member'))
        assert len(clause) == 2                  # alphabetical, then the tiebreak
        manual = Content.order_for(
            type('T', (), {'ordering': 'manual'})())
        rendered = ' '.join(str(part) for part in manual)
    assert 'position' in rendered
    assert 'IS NULL' in rendered                 # the unarranged sort last
    assert rendered.strip().endswith('DESC')     # and the tiebreak is there


def test_promoting_a_block_forgets_where_it_used_to_sit(app, acme):
    """Position means where inside my parent. A row with no parent has no
    inside, so a promoted block must not keep the number it had in the
    course it came out of."""
    from flask import g

    from app.models import Content
    acme.set_type_settings('course', enabled=True)
    acme.set_type_settings('lesson', enabled=True)
    with app.test_request_context():
        g.org = acme
        course = Content(type='course', title='Intro', slug='intro', body='',
                         org_id=acme.id, fields={}, tags=[],
                         visibility='public')
        course.save()
        course.publish()
        lesson = Content(type='lesson', title='Lesson two', slug='two',
                         body='', org_id=acme.id, fields={}, tags=[],
                         visibility='public', parent_id=course.id)
        lesson.save()
        assert lesson.position is not None

        lesson.parent_id = None
        lesson.parent = None
        lesson.save()
        assert lesson.position is None


def test_an_unknown_ordering_is_refused_when_the_type_is_declared(app):
    """A plugin declaring a value nobody implements should be told at
    registration, not fall through to newest-first and look fine."""
    import pytest

    from app.platform.content_types import ContentType
    with pytest.raises(ValueError, match='ordering'):
        ContentType(slug='x', singular='X', plural='Xs', base='/xs',
                    ordering='by-vibes').validate_definition()


def test_every_ordering_ends_in_a_tiebreak(app, acme):
    """Two items sharing a title or a date must not swap places between
    page loads. Asserted for each ordering, because the tiebreak is easy to
    add to one branch and forget in the others."""
    from flask import g

    from app.models import Content
    with app.test_request_context():
        g.org = acme
        for ordering in ('newest', 'oldest', 'alphabetical', 'manual'):
            clause = Content.order_for(
                type('T', (), {'ordering': ordering})())
            assert 'content.id DESC' in str(clause[-1]), ordering
        # ...including the mixed feed, which asks for no type at all.
        assert 'content.id DESC' in str(Content.order_for()[-1])


def test_the_console_list_orders_the_same_way_on_either_database(app, client,
                                                                 acme, user):
    """The console shows drafts beside published items, and a draft has no
    published date. SQLite and PostgreSQL disagree about where a null sorts,
    so without saying where they go the same list reads differently
    depending on which database an installation happens to run.
    """
    from flask import g

    from app.models import Content
    from tests.conftest import login_as
    with app.test_request_context():
        g.org = acme
        published = Content(type='article', title='Published one',
                            slug='published-one', body='', org_id=acme.id,
                            fields={}, tags=[], visibility='public')
        published.save()
        published.publish()
        draft = Content(type='article', title='Still a draft',
                        slug='still-a-draft', body='', org_id=acme.id,
                        fields={}, tags=[], visibility='public')
        draft.save()
        listed = [item.title for item
                  in Content.of_type('article').order_by(
                      *Content.order_for(published.content_type)).all()]
    # Drafts last, wherever the engine would have put a null.
    assert listed[-1] == 'Still a draft', listed
    # Asserted on the clause as well as the rows: SQLite already puts nulls
    # last in a descending sort and PostgreSQL does not, so the rows alone
    # would only catch this on one of the two engines CI runs.
    with app.test_request_context():
        g.org = acme
        for ordering in ('newest', 'oldest', 'manual'):
            clause = ' '.join(str(part) for part in Content.order_for(
                type('T', (), {'ordering': ordering})()))
            assert 'published_at IS NULL' in clause, ordering

    login_as(client, user)
    page = client.get('/manage/content/article',
                      base_url='http://acme.example.test').get_data(as_text=True)
    assert page.index('Published one') < page.index('Still a draft')


def test_the_glossary_reads_alphabetically(app):
    """A glossary that read newest-first would be a glossary in name only.
    Declared by the plugin, so nothing in the core library covers it."""
    from app.platform.content_types import CONTENT_TYPES
    assert CONTENT_TYPES['definition'].ordering == 'alphabetical'
