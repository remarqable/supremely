"""New organizations arrive with a small working website: starter homepage,
About page, navigation, first post, and a General discussion space."""

from flask import g

from app.models import NavigationItem, Organization, Page, Post, Space
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def test_provision_seeds_starter_content(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        home = Page.query.filter_by(slug='home').first()
        about = Page.query.filter_by(slug='about').first()
        assert home is not None and home.is_published
        assert about is not None and about.is_published
        assert acme.setting('homepage_page_id') == home.id
        assert home.created_by_id == user.id

        labels = [i.label for i in NavigationItem.items_for('primary')]
        assert labels == ['Home', 'About', 'Posts', 'Discussions']
        assert [i.label for i in NavigationItem.items_for('footer')] == ['Subscribe']

        post = Post.published_by_slug('hello-world')
        assert post is not None
        assert 'welcome' in post.tags

        space = Space.get_by_slug('general')
        assert space is not None
        assert space.visibility == 'members'


def test_starter_site_renders_for_anonymous(client, acme, globex):
    home = client.get('/', base_url=ACME)
    assert b'Welcome to Acme' in home.data
    assert b'href="/posts"' in home.data            # seeded navigation
    listing = client.get('/posts', base_url=ACME)
    assert b'Hello, World!' in listing.data
    assert client.get('/posts/hello-world', base_url=ACME).status_code == 200
    assert client.get('/about', base_url=ACME).status_code == 200


def test_starter_content_is_per_org(app, client, acme, globex):
    """Same slugs seed independently in every org."""
    acme_home = client.get('/', base_url=ACME)
    globex_home = client.get('/', base_url='http://globex.example.test')
    assert b'Welcome to Acme' in acme_home.data
    assert b'Welcome to Globex' in globex_home.data


def test_owner_can_delete_starter_content(app, client, acme, globex, user):
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        home_id = Page.query.filter_by(slug='home').first().id
    response = client.post(f'/manage/pages/{home_id}/delete', base_url=ACME)
    assert response.status_code == 302
    # Homepage designation cleaned up; site falls back to the org hero
    assert client.get('/', base_url=ACME).status_code == 200
