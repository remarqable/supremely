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


def test_link_without_destination_is_rejected(app, client, acme, globex, user):
    """The old dead-`#`-link mistake: a link must carry a page or a URL."""
    login_as(client, user)
    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'footer', 'kind': 'link', 'label': 'Nowhere'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert NavigationItem.query.filter_by(label='Nowhere').first() is None


def test_explicit_group_ignores_destination_fields(app, client, acme, globex,
                                                   user):
    login_as(client, user)
    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'footer', 'kind': 'group', 'label': 'Legal',
        'url': '/should-be-dropped'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        column = NavigationItem.query.filter_by(label='Legal').first()
        assert column is not None and column.url is None and column.is_group


def test_child_link_needs_destination(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        parent = next(i for i in NavigationItem.items_for('primary')
                      if i.is_group)
        with pytest.raises(ValidationError, match='needs a page or URL'):
            NavigationItem(menu='primary', label='Nowhere',
                           org_id=acme.id, parent_id=parent.id).save()


def test_a_link_cannot_parent_other_links(app, acme):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        link = next(i for i in NavigationItem.items_for('primary')
                    if not i.is_group)
        with pytest.raises(ValidationError, match='inside a group'):
            NavigationItem(menu='primary', label='Child', url='/x',
                           org_id=acme.id, parent_id=link.id).save()


def test_empty_group_is_still_a_group(app, acme):
    """is_group is shape-based (top level, no destination), so a freshly
    created column renders as a column before it has any links."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = NavigationItem(menu='footer', label='Empty', org_id=acme.id)
        group.save()
        assert group.is_group and not group.children


def test_suggested_footer_column_only_when_none_exist(app, client, acme,
                                                      globex, user):
    login_as(client, user)

    def footer_columns():
        with app.test_request_context(base_url=ACME):
            g.org = acme
            return [i for i in NavigationItem.items_for('footer') if i.is_group]

    # Provisioning already created columns: the button must not duplicate.
    before = len(footer_columns())
    assert before > 0
    client.post('/manage/navigation/columns/suggested', base_url=ACME)
    assert len(footer_columns()) == before

    # With every column gone, the button creates the starter column.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        for column in [i for i in NavigationItem.items_for('footer')
                       if i.is_group]:
            column.delete()
    client.post('/manage/navigation/columns/suggested', base_url=ACME)
    columns = footer_columns()
    assert [c.label for c in columns] == ['Explore']
    with app.test_request_context(base_url=ACME):
        g.org = acme
        column = NavigationItem.query.filter_by(label='Explore').first()
        assert [c.label for c in column.children] == ['Blog', 'Community',
                                                      'Newsletter']


def test_navigation_editor_renders_columns_and_bottom_bar(app, client, acme,
                                                          globex, user):
    """The editor mirrors the footer's anatomy: link columns as cards with
    their own add-link forms, and a separate bottom-bar section."""
    login_as(client, user)
    page = client.get('/manage/navigation', base_url=ACME)
    assert page.status_code == 200
    assert b'Footer link columns' in page.data
    assert b'Footer bottom bar' in page.data
    assert b'Add column' in page.data
    assert b'Add dropdown' in page.data
    # Provisioned columns render as cards (Community/Resources/About).
    assert b'Resources' in page.data
    # Default themes support columns: no theme warning.
    assert b'does not display link columns' not in page.data

    # Switch to Trailhead (footer_groups: false): the warning appears.
    acme.theme = 'trailhead'
    from app.extensions import db
    db.session.commit()
    page = client.get('/manage/navigation', base_url=ACME)
    assert b'does not display link columns' in page.data


def test_theme_capabilities_flag(app, acme):
    from app.platform.theming import theme_capabilities
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert theme_capabilities('supremely')['footer_groups'] is True
        assert theme_capabilities('origin')['footer_groups'] is True
        assert theme_capabilities('trailhead')['footer_groups'] is False
        # Unknown/installed themes default to permissive.
        assert theme_capabilities('no-such-theme')['footer_groups'] is True


def test_member_lands_in_community_after_login(app, client, acme, globex, user):
    from tests.conftest import PASSWORD
    response = client.post('/auth/login', base_url=ACME, data={
        'email': user.email, 'password': PASSWORD})
    assert response.status_code == 302
    assert '/dashboard' in response.headers['Location']

    home = client.get('/dashboard', base_url=ACME)
    assert home.status_code == 200
    # The community shell: sidebar nav + feed with the seeded first post.
    assert b'aria-label="Community navigation"' in home.data
    assert b'Discussions' in home.data
    assert b'Hello, World!' in home.data        # seeded article in the feed
