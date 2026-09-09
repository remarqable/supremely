"""The one header the community surface and the console share.

The console used to draw a second header of its own: its links inline on
the left as plain text, and no way back to the homepage at all. Both
places now put the community's name in the middle and one pill on the
right, pointing at whichever place you are not in.
"""

from app.models import Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'

# The header ends where the page does; everything asserted here is inside it.
def navbar(response):
    assert response.status_code == 200
    assert b'<nav' in response.data, 'page rendered without a header'
    return response.data.split(b'<nav')[1].split(b'</nav>')[0]


def name_link(header, name=b'Acme'):
    """The anchor carrying the community's name, attributes only.

    Sliced out rather than searched for across the whole header, because
    the avatar menu prints the reader's own name and email with several of
    the same classes on them."""
    assert b'>' + name + b'<' in header, 'the name is not in the header'
    return header.split(b'>' + name + b'<')[0].rsplit(b'<a ', 1)[1]


def plain_member(app, org, email='plain@example.com'):
    """A member who cannot write content, so no console door is offered."""
    member = make_user(email=email)
    Membership.add(member.id, org.id, role='member')
    client = app.test_client()
    return login_as(client, member)


def test_the_console_offers_the_way_back_to_the_homepage(app, client, acme,
                                                         user):
    login_as(client, user)
    header = navbar(client.get('/manage/content/page', base_url=ACME))
    assert b'Homepage' in header
    assert b'href="/dashboard"' in header


def test_every_page_outside_the_surface_offers_the_way_back(app, acme):
    # Notifications is the page that proves the rule is about being off the
    # community surface rather than about being in the console: it has
    # neither sidebar, and a plain member reaches it. Before there was one
    # header for the surface and one for the console, it was the page a
    # member could get stranded on.
    member = plain_member(app, acme)
    header = navbar(member.get('/notifications/', base_url=ACME))
    assert b'Homepage' in header
    assert b'href="/dashboard"' in header


def test_the_way_back_does_not_require_permission_to_publish(app, acme):
    # A member who cannot open the console still needs the way home, so the
    # pill out here is not gated on writing content.
    member = plain_member(app, acme, email='reader@example.com')
    header = navbar(member.get('/notifications/', base_url=ACME))
    assert b'href="/manage/"' not in header
    assert b'Homepage' in header


def test_the_community_surface_offers_the_way_into_the_console(app, client,
                                                               acme, user):
    login_as(client, user)
    header = navbar(client.get('/dashboard', base_url=ACME))
    assert b'Manage' in header
    assert b'href="/manage/"' in header


def test_neither_pill_points_at_the_page_it_is_on(app, client, acme, user):
    login_as(client, user)
    # One pill, and it always points elsewhere: a header link back to the
    # page you are reading is furniture, not navigation.
    assert b'Manage' not in navbar(client.get('/manage/content/page', base_url=ACME))
    assert b'Homepage' not in navbar(client.get('/dashboard', base_url=ACME))


def test_both_places_center_the_community_name(app, client, acme, user):
    login_as(client, user)
    for path in ('/dashboard', '/manage/content/page'):
        link = name_link(navbar(client.get(path, base_url=ACME)))
        assert b'sm:absolute sm:left-1/2' in link, path
        assert b'sm:-translate-x-1/2' in link, path
        # A phone has no room to centre a name and still clear the pill,
        # the bell and the avatar, so below `sm` it stays in the flow and
        # truncates instead of running underneath them.
        assert b'truncate' in link, path
        assert b'absolute' not in link.split(b'sm:absolute')[0], path


def test_the_console_no_longer_lists_its_sections_in_the_header(app, client,
                                                                acme, user):
    login_as(client, user)
    header = navbar(client.get('/manage/content/page', base_url=ACME))
    # The sidebar owns navigation in the console, as it does on the surface.
    assert b'Dashboard' not in header
    assert b'mobile-menu' not in header


def test_a_member_who_cannot_publish_is_offered_no_console(app, acme):
    member = plain_member(app, acme)
    header = navbar(member.get('/dashboard', base_url=ACME))
    assert b'href="/manage/"' not in header
    # They are already home, so nothing points there either.
    assert b'Homepage' not in header
    assert b'Acme' in header


def test_someone_who_belongs_to_another_community_is_offered_no_way_in(
        app, client, acme, globex):
    # Notifications asks only for a login, so a member of another community
    # can land on this one's copy. The homepage would turn them away, so
    # the header does not point them at it.
    hank = globex.memberships[0].user
    login_as(client, hank)
    assert client.get('/dashboard', base_url=ACME).status_code == 404
    header = navbar(client.get('/notifications/', base_url=ACME))
    assert b'Homepage' not in header
    assert b'href="/dashboard"' not in header


def test_a_visitor_is_offered_neither(app, client, acme):
    header = navbar(client.get('/members', base_url=ACME))
    assert b'href="/manage/"' not in header
    assert b'href="/dashboard"' not in header


def test_the_header_is_a_named_landmark(app, client, acme, user):
    # Two or three nav landmarks render on the same page (this one, the
    # community nav, the console's section nav), so this one says which
    # it is rather than being read out as an unnamed navigation.
    login_as(client, user)
    header = navbar(client.get('/dashboard', base_url=ACME))
    assert b'aria-label="Main"' in header


def test_the_platform_console_names_the_installation(app, client,
                                                     platform_admin):
    # /admin resolves no community, so there is none to name and no pill
    # that would mean anything.
    login_as(client, platform_admin)
    header = navbar(client.get('/admin/'))
    assert b'Supremely' in header
    assert b'href="/manage/"' not in header
    assert b'Homepage' not in header
