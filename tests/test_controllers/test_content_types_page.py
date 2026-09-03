"""Manage -> Content types: the library page and publishing a library type."""

from app.models import Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def test_page_requires_permission(app, client, acme):
    member = make_user(email='plain@example.com')
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/manage/content-types', base_url=ACME).status_code == 403


def test_page_lists_active_and_coming_soon(app, client, acme, user):
    login_as(client, user)
    response = client.get('/manage/content-types', base_url=ACME)
    assert response.status_code == 200
    assert b'Videos' in response.data
    assert b'Podcast' in response.data
    assert b'Resources' in response.data
    assert b'Coming soon' in response.data
    assert b'Jobs' in response.data
    assert b'Courses' in response.data
    # Planned types are placeholders: no manage list behind them.
    assert client.get('/manage/content/job', base_url=ACME).status_code == 404


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
