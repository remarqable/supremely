"""POST /files/upload: one picture for the body somebody is writing.

Shared by the page editor and the discussion composer, so the interesting
questions are who may post to it and what the file it stores is allowed to
be seen by.
"""

import io

from app.models import Membership, Upload
from tests.conftest import login_as, make_png, make_user

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'


def _post(client, data=None, base_url=ACME):
    return client.post('/files/upload', base_url=base_url,
                       data=data if data is not None else
                       {'file': (io.BytesIO(make_png()), 'photo.png')},
                       content_type='multipart/form-data')


def _member(app, org, email='member@example.com'):
    user = make_user(email=email)
    Membership.add(user.id, org.id, role='member')
    return login_as(app.test_client(), user)


def test_an_author_gets_a_public_picture(app, client, acme, user):
    """Whoever publishes the public site is writing a page every visitor
    reads, and a members-only picture behind one is a broken image."""
    login_as(client, user)
    response = _post(client)

    assert response.status_code == 200
    assert response.json['id']
    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.visibility == 'public'


def test_a_members_picture_stays_with_the_members(app, acme, user):
    """A member illustrating a thread is not thereby hosting a public file
    on the organization's domain."""
    member = _member(app, acme)
    response = _post(member)

    assert response.status_code == 200
    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.visibility == 'members'


def test_a_visitor_cannot_upload(app, client, acme):
    assert _post(client).status_code in (302, 403, 404)
    assert Upload.query.count() == 0


def test_somebody_elses_member_cannot_upload_here(app, acme, globex):
    """A membership of one organization is not a membership of the next."""
    outsider = _member(app, globex, email='hank-friend@example.com')
    assert _post(outsider).status_code in (302, 403, 404)
    assert Upload.query.count() == 0


def test_the_picture_belongs_to_the_organization_it_was_posted_to(
        app, client, acme, globex, user):
    login_as(client, user)
    response = _post(client)

    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.org_id == acme.id


def test_a_file_that_is_not_an_image_is_refused_and_not_stored(
        app, client, acme, user):
    """The media library takes a PDF. A body cannot show one, and storing it
    to say no afterwards would be a file written and an id spent."""
    login_as(client, user)
    response = _post(client, data={
        'file': (io.BytesIO(b'%PDF-1.4 handbook'), 'handbook.pdf')})

    assert response.status_code == 400
    assert response.json['error']
    assert Upload.query.count() == 0


def test_nothing_chosen_is_an_answer_not_a_crash(app, client, acme, user):
    login_as(client, user)
    response = _post(client, data={})

    assert response.status_code == 400
    assert response.json['error']


def test_a_description_travels_with_the_file(app, acme, user):
    """A member cannot reach Manage -> Media, so a description set there is
    a description they can never set. Every picture a member inserted was
    going out with an empty alt."""
    member = _member(app, acme)
    response = _post(member, data={
        'file': (io.BytesIO(make_png()), 'photo.png'),
        'alt': '  A lathe in the workshop  '})

    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.alt == 'A lathe in the workshop'


def test_no_description_is_allowed_and_stays_empty(app, acme, user):
    """A picture that is decoration is better with an empty alt than with
    a filename read out in its place."""
    member = _member(app, acme)
    response = _post(member)

    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.alt is None


def test_the_stored_picture_went_through_the_ordinary_pipeline(
        app, client, acme, user):
    """Not restated in the route: variants, sniffing and EXIF stripping come
    from Upload.from_file, and this is the check that it is still what runs."""
    login_as(client, user)
    response = _post(client)

    upload = Upload.query.filter_by(id=response.json['id']).first()
    assert upload.content_type == 'image/png'
    assert upload.has_variants
    assert response.json['url'] == f'/files/{upload.id}/thumb'


def test_the_rate_limit_is_real(app, acme):
    """The only thing bounding writes to the data volume by somebody who is
    not publishing the site. The route's docstring leans on it, so it is
    worth proving it is wired up rather than decorative."""
    from app.middleware.ratelimit import _rate_limits

    member = _member(app, acme)
    app.config['RATELIMIT_ENABLED'] = True
    _rate_limits.clear()
    try:
        codes = [_post(member).status_code for _ in range(14)]
    finally:
        _rate_limits.clear()
        app.config['RATELIMIT_ENABLED'] = False

    assert codes[:12] == [200] * 12
    assert 429 in codes[12:]
    assert Upload.query.count() == 12
