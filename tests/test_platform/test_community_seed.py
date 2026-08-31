"""Provisioning seed content: three groups, six owner posts, overlay hooks."""

import pytest
from flask import g

from app.extensions import db
from app.models import DiscussionGroup, Post
from app.platform.community_seed import (
    DEFAULT_SEED,
    SEED_OVERLAYS,
    register_seed_overlay,
    resolve_seed,
)

ACME = 'http://acme.example.test'

FOOD_OVERLAY = {
    'vertical': 'food',
    'replaces_group': 'Ideas & Feedback',
    'group': {'name': 'Recipes', 'position': 3},
    'posts': [{'title': 'Post a recipe you keep coming back to',
               'body': 'Share the recipe and why it earned its place.'}],
    'overrides': [{'target': "General/What's everyone working on?",
                   'title': 'What did you cook this week?',
                   'body': 'Wins, disasters, and everything in between.'}],
}


def test_provision_seeds_the_default_forum(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        groups = DiscussionGroup.query.order_by(DiscussionGroup.position).all()
        assert [grp.name for grp in groups] == ['Welcome', 'General',
                                                'Ideas & Feedback']
        assert groups[0].visibility == 'public'      # the landing group
        assert groups[1].visibility == 'members'

        posts = Post.query.all()
        assert len(posts) == 6
        # Owner-authored, provision-time timestamps, no system account.
        assert all(post.created_by_id == user.id for post in posts)

        welcome_posts = Post.query.filter_by(group_id=groups[0].id).all()
        assert {post.title for post in welcome_posts} == {
            'Welcome to the community', 'Introduce yourself'}
        assert all(post.is_pinned for post in welcome_posts)
        # Exactly one post carries the seeded flag; nothing else is pinned.
        assert [post.title for post in posts if post.is_seeded] == [
            'Welcome to the community']
        assert all(not post.is_pinned for post in posts
                   if post.group_id != groups[0].id)


def test_seeded_badge_is_owner_only(app, client, acme, user):
    from app.models import Membership
    from tests.conftest import login_as, make_user
    with app.test_request_context(base_url=ACME):
        g.org = acme
        url = Post.query.filter_by(is_seeded=True).one().url

    login_as(client, user)                     # owner
    assert b'>Starter</span>' in client.get(url, base_url=ACME).data

    member = make_user(email='m@example.com')
    Membership.add(member.id, acme.id, role='member')
    member_client = app.test_client()
    login_as(member_client, member)
    assert b'>Starter</span>' not in member_client.get(url, base_url=ACME).data


def test_seeded_posts_are_ordinary_posts(app, client, acme, user):
    """Editable and deletable by the owner with no special-casing."""
    from tests.conftest import login_as
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Post.query.filter_by(is_seeded=True).one()
        url, post_id = post.url, post.id
    response = client.post(f'{url}/edit', base_url=ACME,
                           data={'title': 'Our own welcome', 'body': 'Hi.'})
    assert response.status_code == 302
    response = client.post(f'{url}/delete', base_url=ACME)
    assert response.status_code == 302
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert db.session.get(Post, post_id) is None


# --- Overlay resolver ----------------------------------------------------------

def test_resolver_default_path():
    seed = resolve_seed(None)
    assert seed == DEFAULT_SEED
    assert seed is not DEFAULT_SEED               # never hands out the original


def test_overlay_replaces_group_and_overrides_prompt():
    register_seed_overlay('food', FOOD_OVERLAY)
    try:
        seed = resolve_seed('food')
        names = [grp['name'] for grp in seed['groups']]
        assert names == ['Welcome', 'General', 'Recipes']
        recipes = seed['groups'][2]
        assert recipes['slug'] == 'recipes'
        assert [p['title'] for p in recipes['posts']] == [
            'Post a recipe you keep coming back to']
        general = seed['groups'][1]
        assert general['posts'][0]['title'] == 'What did you cook this week?'
        # The default is never mutated.
        assert DEFAULT_SEED['groups'][2]['name'] == 'Ideas & Feedback'
    finally:
        SEED_OVERLAYS.pop('food', None)


def test_invalid_overlay_falls_back_silently():
    SEED_OVERLAYS['broken'] = {'replaces_group': 'Nope', 'group': {'name': 'X'}}
    try:
        assert resolve_seed('broken') == DEFAULT_SEED
    finally:
        SEED_OVERLAYS.pop('broken', None)


def test_overlay_validation_rejects_bad_shapes():
    with pytest.raises(ValueError):
        register_seed_overlay('x', {'replaces_group': 'General'})  # no group
    with pytest.raises(ValueError):
        register_seed_overlay('x', {
            'replaces_group': 'General', 'group': {'name': 'Y'},
            'posts': [{'title': f't{i}', 'body': 'b'} for i in range(3)]})
    with pytest.raises(ValueError):
        register_seed_overlay('x', {
            'overrides': [{'target': 'General/Not a real post',
                           'title': 'z'}]})
    assert 'x' not in SEED_OVERLAYS
