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


@pytest.fixture
def recipe_type():
    recipe = register_content_type(ContentType(
        slug='recipe', singular='Recipe', plural='Recipes', base='/recipes',
        show_in_nav=True,
        fields=(
            FieldSpec(key='prep_minutes', type='number', label='Prep time',
                      required=True),
            FieldSpec(key='vegetarian', type='boolean', label='Vegetarian'),
        )))
    yield recipe
    ct_module.CONTENT_TYPES.pop('recipe', None)


def test_core_types_registered(app):
    assert {'page', 'article', 'event'} <= set(
        ct_module.CONTENT_TYPES)
    assert ct_module.CONTENT_TYPES['page'].is_page
    assert not ct_module.CONTENT_TYPES['article'].is_page
    assert type_for_base('/blog').slug == 'article'


def test_custom_type_end_to_end(app, client, acme, globex, recipe_type):
    with app.test_request_context():
        g.org = acme
        c = Content(type='recipe', title='Garlic Soup', slug='garlic-soup',
                    body='Boil the garlic.', org_id=acme.id)
        c.set_structured_fields({'prep_minutes': '25', 'vegetarian': 'on'})
        c.save()
        c.publish()
    assert c.fields == {'prep_minutes': 25, 'vegetarian': True}

    # New base routes automatically: archive + single, no core change.
    archive = client.get('/recipes', base_url='http://acme.example.test')
    assert archive.status_code == 200
    assert b'Garlic Soup' in archive.data
    single = client.get('/recipes/garlic-soup',
                        base_url='http://acme.example.test')
    assert single.status_code == 200
    assert b'Boil the garlic.' in single.data


def test_definitions_validated():
    with pytest.raises(ValueError, match='needs a URL base'):
        ContentType(slug='x', singular='X', plural='Xs').validate_definition()
    with pytest.raises(ValueError, match='singular and plural'):
        ContentType(slug='x', singular='', plural='', has_archive=False
                    ).validate_definition()


def test_base_collision_rejected(recipe_type):
    with pytest.raises(ValueError, match='base'):
        register_content_type(ContentType(
            slug='recipe2', singular='R2', plural='R2s', base='/recipes'))


def test_duplicate_slug_rejected(recipe_type):
    with pytest.raises(ValueError, match='already registered'):
        register_content_type(ContentType(
            slug='recipe', singular='Again', plural='Agains', base='/r2'))
