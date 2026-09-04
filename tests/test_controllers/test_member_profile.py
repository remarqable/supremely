"""Member profile pages: a member's name links to /members/<id>, which shows
their bio and recent posts to fellow members and nothing to anyone else."""

from flask import g

from app.models import Membership, Post
from tests.conftest import login_as, make_user
from tests.test_controllers.test_discussions import create_post, make_group

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'


def make_member(app, org, email, name, bio=None):
    user = make_user(email=email, name=name)
    if bio:
        user.bio = bio
        user.save()
    Membership.add(user.id, org.id, role='member')
    return user


def test_member_sees_profile_with_bio_and_posts(app, client, acme, globex, user):
    make_group(app, acme)
    alice = make_member(app, acme, 'alice@example.com', 'Alice Example',
                        bio='Gardener. Asks too many questions.')
    alice_client = login_as(app.test_client(), alice)
    create_post(alice_client, title='Tomato blight help')

    login_as(client, user)
    page = client.get(f'/members/{alice.id}', base_url=ACME)
    assert page.status_code == 200
    assert b'Alice Example' in page.data
    assert b'Asks too many questions' in page.data
    assert b'Tomato blight help' in page.data
    # Viewing someone else: no edit control.
    assert b'Edit profile' not in page.data


def test_own_profile_offers_edit(client, acme, globex, user):
    login_as(client, user)
    page = client.get(f'/members/{user.id}', base_url=ACME)
    assert page.status_code == 200
    assert b'Edit profile' in page.data
    assert b'href="/profile"' in page.data


def test_visitor_gets_the_gate_not_the_bio(app, acme, globex, user):
    user.bio = 'Secret bio'
    user.save()
    anon = app.test_client()
    page = anon.get(f'/members/{user.id}', base_url=ACME)
    assert page.status_code == 200
    assert b'Members only' in page.data
    assert b'Secret bio' not in page.data
    assert user.name.encode() not in page.data


def test_gate_degrades_to_login_redirect_when_teasing_is_off(app, acme, globex, user):
    acme.update_settings(gated_teasers=False)
    anon = app.test_client()
    page = anon.get(f'/members/{user.id}', base_url=ACME)
    assert page.status_code == 302
    assert '/auth/login' in page.headers['Location']


def test_profile_is_scoped_to_the_organization(app, client, acme, globex, user):
    """An acme member's profile does not exist on globex's site, and a
    globex member looking at acme sees the gate, not the profile."""
    hank = globex.memberships[0].user
    login_as(client, user)                       # acme owner
    assert client.get(f'/members/{hank.id}', base_url=ACME).status_code == 404
    hank_client = login_as(app.test_client(), hank)
    page = hank_client.get(f'/members/{user.id}', base_url=ACME)
    assert page.status_code == 200
    assert b'Members only' in page.data
    assert user.name.encode() not in page.data


def test_suspended_and_unknown_members_are_404(app, client, acme, globex, user):
    login_as(client, user)
    bob = make_member(app, acme, 'bob@example.com', 'Bob')
    assert client.get(f'/members/{bob.id}', base_url=ACME).status_code == 200
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Membership.get(bob.id, acme.id).suspend()
    assert client.get(f'/members/{bob.id}', base_url=ACME).status_code == 404
    assert client.get('/members/999999', base_url=ACME).status_code == 404


def test_hidden_posts_shown_to_moderators_only(app, client, acme, globex, user):
    make_group(app, acme)
    alice = make_member(app, acme, 'alice@example.com', 'Alice')
    alice_client = login_as(app.test_client(), alice)
    create_post(alice_client, title='Visible post')
    create_post(alice_client, title='Hidden post')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        hidden = Post.query.filter_by(title='Hidden post').one()
        hidden.is_hidden = True
        hidden.save()

    carol_client = login_as(app.test_client(),
                            make_member(app, acme, 'carol@example.com', 'Carol'))
    page = carol_client.get(f'/members/{alice.id}', base_url=ACME)
    assert b'Visible post' in page.data
    assert b'Hidden post' not in page.data

    login_as(client, user)                       # owner moderates
    page = client.get(f'/members/{alice.id}', base_url=ACME)
    assert b'Hidden post' in page.data


def test_posts_from_another_organization_stay_out(app, client, acme, globex, user):
    """The same account can belong to two organizations; each profile lists
    only the posts made on that site."""
    make_group(app, acme)
    make_group(app, globex)
    alice = make_member(app, acme, 'alice@example.com', 'Alice')
    Membership.add(alice.id, globex.id)
    alice_client = login_as(app.test_client(), alice)
    create_post(alice_client, title='Said on acme')
    alice_client.post('/discussions/general/new', base_url=GLOBEX,
                      data={'title': 'Said on globex', 'body': 'Hi'})

    login_as(client, user)
    page = client.get(f'/members/{alice.id}', base_url=ACME)
    assert b'Said on acme' in page.data
    assert b'Said on globex' not in page.data


def test_names_link_to_profiles_across_the_surface(app, client, acme, globex, user):
    make_group(app, acme)
    alice = make_member(app, acme, 'alice@example.com', 'Alice Example')
    alice_client = login_as(app.test_client(), alice)
    create_post(alice_client, title='Linked post')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Post.query.filter_by(title='Linked post').one()
        post_url = post.url
    client.post(f'{post_url}/reply', base_url=ACME, data={'body': 'A reply'})

    login_as(client, user)
    link = f'href="/members/{alice.id}"'.encode()
    # The post page: post author and, further down, the directory card.
    assert link in client.get(post_url, base_url=ACME).data
    # The group listing and the discussions index.
    assert link in client.get('/discussions/general', base_url=ACME).data
    assert link in client.get('/discussions/', base_url=ACME).data
    # The directory itself.
    assert link in client.get('/members', base_url=ACME).data
    # The reply author (the owner) is linked on the post page too.
    assert f'href="/members/{user.id}"'.encode() in client.get(post_url, base_url=ACME).data
