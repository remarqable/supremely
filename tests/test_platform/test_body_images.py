"""The :::image directive: a picture from the media library, in a body.

Plain Markdown already put an image in a body, because `img` is in the
allowlist. What it could not say is where the picture sits, and the picture
carried its own description rather than the media library's. The directive is
how both of those get answered, and the markup around them is written by the
renderer rather than by whoever is typing.
"""

import io
import re

import pytest
from flask import g

from app.extensions import db
from app.models import Membership, Upload
from app.platform.content import _MAX_IMAGES as MAX_IMAGES
from app.platform.content import PLACEMENTS, render_markdown
from tests.conftest import make_png


class _Part:
    """A werkzeug FileStorage, near enough for Upload.from_file."""

    def __init__(self, data: bytes, filename: str = 'photo.png'):
        self.stream = io.BytesIO(data)
        self.filename = filename


def _upload(alt=None, data=None, filename='photo.png') -> int:
    from app.extensions import db
    upload = Upload.from_file(_Part(data or make_png(), filename))
    upload.alt = alt
    upload.save()
    db.session.commit()
    return upload.id


def _make_members_only(upload_id: int) -> None:
    upload = Upload.query.filter_by(id=upload_id).first()
    upload.visibility = 'members'
    upload.save()
    db.session.commit()


def test_a_picture_lands_in_the_body_with_its_description(app, acme):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload(alt='The lathe bay')
        html = render_markdown(f'Before.\n\n:::image {upload_id}\n\nAfter.')

    assert f'src="/files/{upload_id}/full"' in html
    assert 'alt="The lathe bay"' in html
    assert 'body-image--center' in html


def test_the_figure_sits_beside_the_prose_not_inside_a_paragraph(app, acme):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(f'Before.\n\n:::image {upload_id}\n\nAfter.')

    assert '<p>Before.</p>' in html and '<p>After.</p>' in html
    assert '<p><figure' not in html


@pytest.mark.parametrize('placement', PLACEMENTS)
def test_every_placement_is_carried_on_the_figure(app, acme, placement):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(f':::image {upload_id} {placement}')

    assert f'class="body-image body-image--{placement}"' in html


def test_a_floated_picture_reads_the_smaller_variant(app, acme):
    """A picture a third of a column wide has no use for 1600 pixels."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        floated = render_markdown(f':::image {upload_id} left')
        centred = render_markdown(f':::image {upload_id} center')

    assert f'/files/{upload_id}/medium' in floated
    assert f'/files/{upload_id}/full' in centred


def test_a_word_that_is_not_a_placement_leaves_the_line_alone(app, acme):
    """The same answer an unrecognised video link gets: it was never a
    directive, so it is the text somebody typed."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(f':::image {upload_id} sideways')

    assert '<figure' not in html
    assert ':::image' in html


def test_several_pictures_in_one_body_all_arrive(app, acme):
    with app.test_request_context():
        g.org = acme
        first, second, third = (_upload() for _ in range(3))
        html = render_markdown(f':::image {first} left\n\nWords.\n\n'
                               f':::image {second} right\n\nMore.\n\n'
                               f':::image {third} wide')

    for upload_id in (first, second, third):
        assert f'/files/{upload_id}/' in html
    assert html.count('<figure') == 3


def test_a_deleted_picture_leaves_nothing_behind(app, acme):
    """A body outlives the media library. Removing a file must not leave an
    article showing a torn-page icon."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        Upload.query.filter_by(id=upload_id).first().delete()
        html = render_markdown(f'Before.\n\n:::image {upload_id}\n\nAfter.')

    assert '<figure' not in html and '<img' not in html
    assert ':::image' not in html
    assert '<p>Before.</p>' in html and '<p>After.</p>' in html


def test_another_organizations_picture_is_not_reachable(app, acme, globex):
    """The id is typed into a body, which is no more trustworthy than a
    form. The lookup is a real scoped query, so the tenant filter answers."""
    with app.test_request_context():
        g.org = globex
        theirs = _upload()

    with app.test_request_context():
        g.org = acme
        html = render_markdown(f':::image {theirs}')

    assert '<img' not in html


def test_a_file_that_is_not_an_image_renders_nothing(app, acme):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload(data=b'%PDF-1.4 whatever',
                            filename='handbook.pdf')
        html = render_markdown(f':::image {upload_id}')

    assert '<img' not in html


def test_a_discussion_body_gets_pictures_too(app, acme):
    """Discussion posts render with directives='ignore', because :::embed
    and :::feed are not theirs. An image is, so it is stashed with the video
    rather than gated with those two."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(f':::image {upload_id}', directives='ignore')

    assert '<figure' in html


def test_the_directive_must_be_on_a_line_of_its_own(app, acme):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(f'see :::image {upload_id} left inline')

    assert '<figure' not in html


@pytest.mark.parametrize('newline', ['\r\n', '\r', '\n'])
def test_the_directive_survives_the_line_endings_a_browser_sends(
        app, acme, newline):
    """A textarea is submitted with CRLF, so every body saved through an
    editor has them."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        body = newline.join([f':::image {upload_id} wide', '', 'Text.'])
        html = render_markdown(body)

    assert 'body-image--wide' in html
    assert ':::image' not in html


def test_an_author_cannot_forge_the_internal_marker(app, acme):
    """The marker carries a per-render random token, so text that looks like
    one is not swapped for a picture."""
    with app.test_request_context():
        g.org = acme
        html = render_markdown('supremelyimagedeadbeefdeadbeef0')

    assert '<img' not in html
    assert 'supremelyimagedeadbeefdeadbeef0' in html


def test_a_raw_figure_tag_still_cannot_carry_a_class(app, acme):
    """The placement classes are ours. Writing one by hand does not get a
    picture floated, because the cleaner runs before our markup is spliced
    in and `figure` has no allowed attributes."""
    html = render_markdown('<figure class="body-image body-image--wide">'
                           '<img src="/files/1/full"></figure>')
    assert 'body-image--wide' not in html


def test_an_email_gets_the_whole_address(app, acme):
    """A message carries no origin to resolve /files/42/full against."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        html = render_markdown(
            f':::image {upload_id}',
            absolute_url=lambda path: f'https://acme.example.test{path}')

    assert f'src="https://acme.example.test/files/{upload_id}/full"' in html


def test_a_summary_is_unbothered_by_a_picture(app, acme):
    """A listing summarises words. A body that opens with a photograph
    should not summarise as an empty string."""
    from app.models.discussion import Post

    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        post = Post(title='x', body=f':::image {upload_id}\n\nThe words.')
        assert post.summary() == 'The words.'

def test_a_members_only_picture_is_left_out_for_a_visitor(app, acme):
    """The body agrees with what /files/<id> will serve. A visitor used to
    get a broken box with the admin's description of it underneath, which is
    also how anyone who could write a body learned which ids existed."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload(alt='The members-only lathe')
        _make_members_only(upload_id)
        html = render_markdown(f':::image {upload_id}')

    assert '<img' not in html
    assert 'members-only lathe' not in html


def test_a_member_still_sees_it(app, acme, user):
    from flask_login import login_user

    with app.test_request_context():
        g.org = acme
        g.membership = Membership.get(user.id, acme.id)
        login_user(user)
        upload_id = _upload()
        _make_members_only(upload_id)
        html = render_markdown(f':::image {upload_id}')

    assert '<img' in html


def test_with_no_organization_in_force_nothing_resolves(app, acme):
    """The tenant filter does not apply where there is no tenant to apply
    it for, so the lookup has to refuse rather than reach every
    organization's files. Fails closed like can_view and serve_upload."""
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()

    # No request, no ambient organization: the command line and migrations.
    html = render_markdown(f':::image {upload_id}')
    assert '<img' not in html


def test_one_query_however_many_pictures(app, acme):
    """A body is rendered on every page view, and a discussion body is
    written by any member. A query per directive let one saved post cost
    thousands of them."""
    from sqlalchemy import event

    with app.test_request_context():
        g.org = acme
        ids = [_upload() for _ in range(6)]
        body = '\n\n'.join(f':::image {upload_id}' for upload_id in ids)

        seen = []
        engine = db.session.get_bind()

        def count(conn, cursor, statement, *args):
            if 'upload' in statement.lower():
                seen.append(statement)

        event.listen(engine, 'before_cursor_execute', count)
        try:
            html = render_markdown(body)
        finally:
            event.remove(engine, 'before_cursor_execute', count)

    assert html.count('<figure') == 6
    assert len(seen) == 1, seen


def test_past_the_cap_the_rest_render_as_nothing(app, acme):
    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        body = '\n\n'.join([f':::image {upload_id}'] * (MAX_IMAGES + 5))
        html = render_markdown(body)

    assert html.count('<figure') == MAX_IMAGES
    assert ':::image' not in html


def test_the_eleventh_picture_is_the_eleventh_picture(app, acme):
    """The markers end in a number and a shorter number is a prefix of a
    longer one, so replacing marker 1 while marker 10 still stood put the
    second picture where the eleventh belonged and left its trailing 0 in
    the prose."""
    with app.test_request_context():
        g.org = acme
        ids = [_upload() for _ in range(12)]
        body = '\n\n'.join(f':::image {upload_id}' for upload_id in ids)
        html = render_markdown(body)

    assert html.count('<figure') == 12
    order = re.findall(r'/files/(\d+)/', html)
    assert order == [str(upload_id) for upload_id in ids]
    assert 'supremelyimage' not in html


def test_a_summary_asks_for_no_pictures_at_all(app, acme):
    """A listing draws thirty summaries and every one used to spend a query
    resolving figures that the tag stripping on the next line threw away."""
    from sqlalchemy import event

    from app.models.discussion import Post

    with app.test_request_context():
        g.org = acme
        upload_id = _upload()
        post = Post(title='x', body=f':::image {upload_id} left\n\nThe words.')

        seen = []
        engine = db.session.get_bind()

        def count(conn, cursor, statement, *args):
            if 'upload' in statement.lower():
                seen.append(statement)

        event.listen(engine, 'before_cursor_execute', count)
        try:
            summary = post.summary()
        finally:
            event.remove(engine, 'before_cursor_execute', count)

    assert summary == 'The words.'
    assert seen == []


def test_a_body_naming_thousands_of_files_asks_for_a_bounded_number(app, acme):
    """The cap has to cut before the query, not while substituting. Every
    distinct id became a bound parameter, and SQLite before 3.32 caps those
    at 999 and raises rather than truncating -- one saved post returning
    500 for good on a self-hosted installation."""
    from app.platform.content import _wanted_ids

    body = '\n\n'.join(f':::image {n}' for n in range(1, 3001))
    assert len(_wanted_ids(body)) == MAX_IMAGES


def test_the_same_picture_many_times_is_asked_for_once(app, acme):
    from app.platform.content import _wanted_ids

    assert _wanted_ids('\n\n'.join([':::image 7 left'] * 100)) == {7}

