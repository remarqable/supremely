"""Manage -> Content types: the library page and publishing a library type."""

from app.models import Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def test_page_requires_permission(app, client, acme):
    member = make_user(email='plain@example.com')
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/manage/content-types', base_url=ACME).status_code == 403


def test_page_lists_every_type_the_library_offers(app, client, acme, user):
    login_as(client, user)
    response = client.get('/manage/content-types', base_url=ACME)
    assert response.status_code == 200
    assert b'Videos' in response.data
    assert b'Podcast' in response.data
    assert b'Resources' in response.data
    # Jobs and Courses are real types, listed here whether or not this
    # organization publishes them. They are off until asked for, so the
    # lists behind them do not answer yet.
    assert b'Jobs' in response.data
    assert b'Courses' in response.data
    for slug in ('job', 'course'):
        assert client.get(f'/manage/content/{slug}',
                          base_url=ACME).status_code == 404, slug


def test_nothing_is_coming_soon_so_nothing_says_so(app, client, acme, user):
    """The placeholder list is empty now that every type it was holding back
    has shipped. An empty section with a heading over it says a thing is on
    the way when nothing is."""
    login_as(client, user)
    response = client.get('/manage/content-types', base_url=ACME)
    assert b'Coming soon' not in response.data


def test_publish_and_view_recording(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/content/recording/new', base_url=ACME,
                           data={'title': 'Member Deep Dive',
                                 'slug': 'member-deep-dive',
                                 'body': 'A great **session**.',
                                 'visibility': 'public', 'action': 'publish',
                                 'field_video_url': 'https://example.com/v/9'})
    assert response.status_code == 302

    archive = client.get('/recordings', base_url=ACME)
    assert archive.status_code == 200
    assert b'Member Deep Dive' in archive.data

    single = client.get('/recordings/member-deep-dive', base_url=ACME)
    assert single.status_code == 200
    assert b'session' in single.data


def test_recording_field_validation(app, client, acme, user):
    login_as(client, user)
    response = client.post('/manage/content/recording/new', base_url=ACME,
                           data={'title': 'No video', 'slug': 'no-video',
                                 'body': 'x', 'visibility': 'public',
                                 'action': 'publish',
                                 'field_video_url': 'not-a-url'})
    assert response.status_code == 200            # re-rendered form
    assert b'must be an http(s) URL' in response.data


def test_the_videos_archive_keeps_its_recordings_url(client, acme):
    """The other half of the rename: the label moved, the URL did not, so
    a link published before the rename still resolves."""
    archive = client.get('/recordings', base_url=ACME)
    assert archive.status_code == 200
    assert b'Videos' in archive.data
    assert b'Recordings' not in archive.data
