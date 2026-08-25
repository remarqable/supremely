"""New organizations arrive with a small working website: starter homepage,
About/FAQ/Contact pages, navigation, a first article, an example Event, and a
General discussion space."""

from flask import g

from app.models import Content, NavigationItem, Organization, Space
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def _page(slug):
    return Content.query.filter_by(type='page', slug=slug).first()


def test_provision_seeds_starter_content(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        home = _page('home')
        about = _page('about')
        assert home is not None and home.is_published
        assert about is not None and about.is_published
        assert acme.setting('homepage_content_id') == home.id
        assert home.created_by_id == user.id

        primary = NavigationItem.items_for('primary')
        assert [i.label for i in primary] == ['Home', 'Community',
                                              'Resources', 'About']
        community = primary[1]
        assert community.is_group
        assert [c.label for c in community.children] == ['Discussions', 'Members']
        assert [c.href for c in primary[2].children] == ['/blog', '/subscribe']
        about_group = primary[3]
        assert [c.label for c in about_group.children] == ['About', 'FAQ', 'Contact']

        footer = NavigationItem.items_for('footer')
        assert [i.label for i in footer] == ['Community', 'Resources', 'About']
        assert all(i.is_group for i in footer)

        assert _page('faq').is_published
        assert _page('contact').is_published
        assert acme.setting('member_directory') is True

        article = Content.published_by_slug('article', 'hello-world')
        assert article is not None
        assert 'welcome' in article.tags

        # The Event is a visible hint that vertical content types exist.
        event = Content.published_by_slug('event', 'kickoff-meetup')
        assert event is not None
        assert event.fields.get('starts_on')

        space = Space.get_by_slug('general')
        assert space is not None
        assert space.visibility == 'members'


def test_starter_site_renders_for_anonymous(client, acme, globex):
    home = client.get('/', base_url=ACME)
    assert b'Welcome to Acme' in home.data
    assert b'href="/blog"' in home.data             # seeded navigation
    assert b'aria-haspopup="menu"' in home.data     # dropdown groups render
    assert home.data.count(b'href="/discussions"') >= 2   # header + footer
    listing = client.get('/blog', base_url=ACME)
    assert b'Hello, World!' in listing.data
    assert client.get('/blog/hello-world', base_url=ACME).status_code == 200
    assert client.get('/events/kickoff-meetup', base_url=ACME).status_code == 200
    for slug in ('about', 'faq', 'contact'):
        assert client.get(f'/{slug}', base_url=ACME).status_code == 200


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
        home_id = _page('home').id
    response = client.post(f'/manage/content/{home_id}/delete', base_url=ACME)
    assert response.status_code == 302
    # Homepage designation cleaned up; site falls back to the org hero
    assert client.get('/', base_url=ACME).status_code == 200
