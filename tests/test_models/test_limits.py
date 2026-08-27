"""Length and character limits.

Two reasons these live in validate() rather than in the column: SQLite does not
enforce a declared width, so an over-long value stores in development and
raises in production; and a body is re-rendered through Markdown and the
sanitiser on every view, so an unbounded one is a way to make a page expensive
to serve.
"""

import pytest
from flask import g

from app.models import Content, Organization
from app.models.discussion import BODY_MAX, DiscussionGroup, Flag, Post, Reply
from app.models.newsletter import Subscriber
from app.platform.errors import ValidationError


def test_a_discussion_body_is_bounded(app, acme):
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too long'):
            Post(org_id=acme.id, title='T', body='x' * (BODY_MAX + 1),
                 group_id=1).validate()
        with pytest.raises(ValidationError, match='too long'):
            Reply(org_id=acme.id, body='x' * (BODY_MAX + 1),
                  post_id=1).validate()

        Post(org_id=acme.id, title='T', body='x' * BODY_MAX,
             group_id=1).validate()


@pytest.mark.parametrize('field,limit', [
    ('excerpt', 500),
    ('seo_title', 200),
    ('seo_description', 300),
])
def test_content_text_fields_are_bounded(app, acme, field, limit):
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too long'):
            Content(org_id=acme.id, type='page', title='T', slug='limit-probe',
                    body='b', **{field: 'y' * (limit + 1)}).validate()

        Content(org_id=acme.id, type='page', title='T', slug='limit-probe',
                body='b', **{field: 'y' * limit}).validate()


def test_a_group_description_and_a_flag_reason_are_bounded(app, acme):
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too long'):
            DiscussionGroup(org_id=acme.id, name='G', slug='limit-g',
                            description='y' * 501).validate()
        with pytest.raises(ValidationError, match='too long'):
            Flag(org_id=acme.id, target_type='post', target_id=1,
                 reason='y' * 501).validate()


def test_a_subscriber_email_is_bounded(app, acme):
    """Anonymously reachable, and the column is 255: PostgreSQL raises where
    SQLite quietly stores it."""
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too long'):
            Subscriber(org_id=acme.id, email='a' * 290 + '@example.com',
                       status='subscribed', token='t').validate()


@pytest.mark.parametrize('value', [
    'Acme\r\nBcc: someone@example.com',
    'Acme\nBcc: someone@example.com',
    'Acme\x00null',
])
def test_a_name_that_cannot_travel_in_a_header_is_refused(app, acme, value):
    """These reach an email subject. The standard library refuses a line break
    there, so the whole message raises instead of one recipient failing, and
    the job burns its retries."""
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='line breaks'):
            Organization(name=value, slug='header-probe').validate()
        with pytest.raises(ValidationError, match='line breaks'):
            Post(org_id=acme.id, title=value, body='b', group_id=1).validate()


def test_a_tab_is_still_allowed(app, acme):
    with app.test_request_context():
        g.org = acme
        Organization(name='Acme\tCommunity', slug='tab-probe').validate()


def test_a_theme_url_field_refuses_a_scripting_scheme(app):
    """These are rendered straight into an href. Autoescaping stops markup
    breaking out of the attribute and says nothing about the scheme, so today
    only the Content-Security-Policy stands between an editor and every
    visitor to the public page."""
    from app.platform.theme_content import _safe_url

    for hostile in ('javascript:alert(1)', 'data:text/html,x', 'vbscript:x'):
        assert _safe_url(hostile) == ''

    for allowed in ('https://example.com', 'http://example.com', '/local',
                    '#section'):
        assert _safe_url(allowed) == allowed


def test_a_group_name_and_a_content_body_are_bounded(app, acme):
    from app.models.content import BODY_MAX as CONTENT_BODY_MAX

    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='too long'):
            DiscussionGroup(org_id=acme.id, name='y' * 101,
                            slug='limit-n').validate()
        with pytest.raises(ValidationError, match='too long'):
            Content(org_id=acme.id, type='page', title='T', slug='limit-b',
                    body='y' * (CONTENT_BODY_MAX + 1)).validate()


def test_a_content_title_that_cannot_travel_in_a_header_is_refused(app, acme):
    """Content.title is the newsletter subject line, so it needs the same
    guard as a discussion title."""
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='line breaks'):
            Content(org_id=acme.id, type='article', title='Hi\r\nBcc: x@y.z',
                    slug='hdr-probe', body='b').validate()


def test_the_editor_form_refuses_an_offsite_or_scripting_link(app, acme):
    """Driven through clean(), because testing the helper alone would stay
    green if nothing called it."""
    from app.platform import theme_content as tc

    with app.test_request_context():
        g.org = acme
        url_fields = [f['key'] for f in tc.schema('origin')
                      if f['type'] == 'url']
        assert url_fields, 'expected the origin theme to declare a url field'
        key = url_fields[0]

        for hostile in ('javascript:alert(1)', '//evil.example',
                        '/\\evil.example', 'data:text/html,x'):
            assert tc.clean('origin', {key: hostile})[key] == ''

        assert tc.clean('origin', {key: '/local'})[key] == '/local'
        assert tc.clean('origin', {key: 'HTTPS://ok.example'})[key] == \
            'HTTPS://ok.example'
