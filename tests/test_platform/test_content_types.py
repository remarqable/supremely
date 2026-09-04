"""Content Types: a developer defines a new vertical without touching the
Content subsystem."""

import pytest
from flask import g

from app.models import Content
from app.platform import content_types as ct_module
from app.platform.content_types import (
    ContentType,
    FieldSpec,
    register_content_type,
    type_for_base,
)
from app.platform.errors import ValidationError


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
        ContentType(slug='x', singular='', plural='', has_archive=False
                    ).validate_definition()


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
