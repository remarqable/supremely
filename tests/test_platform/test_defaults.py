"""New organizations arrive with a small working website: an editable home
page (the theme's hero), About/FAQ/Contact pages, navigation, a first article,
an example Event, and a General discussion space."""

from flask import g

from app.models import Content, NavigationItem, Space
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def _page(slug):
    return Content.query.filter_by(type='page', slug=slug).first()


def test_provision_seeds_starter_content(app, acme, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        about = _page('about')
        assert about is not None and about.is_published
        assert about.created_by_id == user.id
        # The home page is the theme's hero, not a CMS page — no 'home' page,
        # and starter copy is seeded for Origin's hero.
        assert _page('home') is None
        assert acme.setting('homepage_content_id') is None
        assert acme.setting('theme_content')['origin']['subhead']

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
    assert b'Acme' in home.data                      # hero headline = org name
    assert b'grow together' in home.data             # seeded hero subhead
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
    """Each org's home page shows its own name."""
    acme_home = client.get('/', base_url=ACME)
    globex_home = client.get('/', base_url='http://globex.example.test')
    assert b'Acme' in acme_home.data
    assert b'Globex' in globex_home.data


def test_owner_can_delete_starter_content(app, client, acme, globex, user):
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        about_id = _page('about').id
    response = client.post(f'/manage/content/{about_id}/delete', base_url=ACME)
    assert response.status_code == 302
    assert client.get('/about', base_url=ACME).status_code == 404
    assert client.get('/', base_url=ACME).status_code == 200
