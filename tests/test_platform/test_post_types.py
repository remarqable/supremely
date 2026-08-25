"""Phase 3 completion test: a developer defines a new structured Post Type
without modifying Supremely's Post subsystem."""

import pytest
from flask import g

from app.extensions import db
from app.models import Post
from app.platform import post_types as pt_module
from app.platform.errors import ValidationError
from app.platform.post_types import FieldSpec, PostType, register_post_type


@pytest.fixture
def recipe_type():
    """A brand-new vertical, defined entirely outside the Post subsystem."""
    recipe = register_post_type(PostType(
        slug='recipe', name='Recipe',
        fields=(
            FieldSpec(key='prep_minutes', type='number', label='Prep time',
                      required=True),
            FieldSpec(key='servings', type='number', label='Servings'),
            FieldSpec(key='vegetarian', type='boolean', label='Vegetarian'),
        ),
        template='post-recipe',
    ))
    yield recipe
    pt_module.POST_TYPES.pop('recipe', None)


def test_custom_type_end_to_end(app, client, acme, globex, recipe_type):
    with app.test_request_context():
        g.org = acme
        post = Post(title='Garlic Soup', slug='garlic-soup',
                    body='Boil the garlic.', org_id=acme.id, type='recipe')
        post.set_structured_fields({'prep_minutes': '25', 'servings': '4',
                                    'vegetarian': 'on'})
        post.save()
        post.publish()

    assert post.fields == {'prep_minutes': 25, 'servings': 4,
                           'vegetarian': True}

    # Renders through the generic fallback because no post-recipe template
    # exists -- the hierarchy absorbs new types with zero core changes.
    response = client.get('/posts/garlic-soup',
                          base_url='http://acme.example.test')
    assert response.status_code == 200
    assert b'Garlic Soup' in response.data
    assert b'Boil the garlic.' in response.data


def test_custom_type_field_validation(app, acme, recipe_type):
    with app.test_request_context():
        g.org = acme
        post = Post(title='X', slug='x', org_id=acme.id, type='recipe')
        with pytest.raises(ValidationError, match='Prep time'):
            post.set_structured_fields({'servings': '2'})
        with pytest.raises(ValidationError, match='must be a number'):
            post.set_structured_fields({'prep_minutes': 'soon'})


def test_type_definitions_validated():
    with pytest.raises(ValueError, match='Invalid post type slug'):
        PostType(slug='Bad Slug', name='X').validate_definition()
    with pytest.raises(ValueError, match='Duplicate field key'):
        PostType(slug='ok', name='X', fields=(
            FieldSpec(key='a'), FieldSpec(key='a'),
        )).validate_definition()


def test_duplicate_registration_rejected(recipe_type):
    with pytest.raises(ValueError, match='already registered'):
        register_post_type(PostType(slug='recipe', name='Again'))
