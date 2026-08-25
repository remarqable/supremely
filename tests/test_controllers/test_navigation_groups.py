"""Dropdown navigation groups: one level, managed in /manage/navigation."""

import pytest
from flask import g

from app.extensions import db
from app.models import NavigationItem
from app.platform.errors import ValidationError
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def test_create_group_and_child_via_manage(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'primary', 'label': 'Programs'})       # no url, no page: a group
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = next(i for i in NavigationItem.items_for('primary')
                     if i.label == 'Programs')
        group_id = group.id

    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'primary', 'label': 'Mentorship', 'url': '/mentorship',
        'parent_id': group_id})

    home = client.get('/', base_url=ACME)
    assert b'Programs' in home.data
    assert b'href="/mentorship"' in home.data

    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = db.session.get(NavigationItem, group_id)
        assert [c.label for c in group.children] == ['Mentorship']


def test_second_level_nesting_rejected(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        parent = next(i for i in NavigationItem.items_for('primary')
                      if i.is_group)
        child = parent.children[0]
        with pytest.raises(ValidationError, match='one level'):
            NavigationItem(menu='primary', label='Deep', url='/x',
                           org_id=acme.id, parent_id=child.id).save()


def test_group_deletion_cascades_children(app, client, acme, globex, user):
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = next(i for i in NavigationItem.items_for('primary')
                     if i.label == 'Resources')
        group_id, child_count = group.id, len(group.children)
        assert child_count > 0
        total_before = NavigationItem.query.count()

    client.post(f'/manage/navigation/{group_id}/delete', base_url=ACME)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert NavigationItem.query.count() == total_before - 1 - child_count


def test_member_lands_in_community_after_login(app, client, acme, globex, user):
    from tests.conftest import PASSWORD
    response = client.post('/auth/login', base_url=ACME, data={
        'email': user.email, 'password': PASSWORD})
    assert response.status_code == 302
    assert '/dashboard' in response.headers['Location']

    home = client.get('/dashboard', base_url=ACME)
    assert home.status_code == 200
    assert b'Recent activity' in home.data
    assert b'General' in home.data              # seeded space in the left rail
    assert b'Hello, World!' in home.data        # seeded post in announcements
