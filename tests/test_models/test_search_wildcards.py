"""LIKE has wildcards of its own.

The search term was always bound as a parameter, so this was never injection.
It was a search that did not mean what the visitor typed: a query of "%"
matched every row and scanned the whole table.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Content
from app.models.base import escape_like
from tests.conftest import login_as

ACME = 'http://acme.example.test'


@pytest.mark.parametrize('term,expected', [
    ('%', r'\%'),
    ('A_me', r'A\_me'),
    ('100%_sure', r'100\%\_sure'),
    ('plain', 'plain'),
    ('\\', '\\\\'),
])
def test_wildcards_in_a_term_are_neutralised(term, expected):
    assert escape_like(term) == expected


def _tagged(app, acme, tag):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        article = Content.query.filter_by(org_id=acme.id, type='article').first()
        article.tags = [tag]
        article.status = 'published'
        db.session.add(article)
        db.session.commit()
        return article.title


def test_a_tag_of_percent_does_not_match_every_tagged_post(app, client, acme):
    title = _tagged(app, acme, 'security')

    wildcard = client.get('/blog/tag/%25', base_url=ACME)
    assert title.encode() not in wildcard.data

    real = client.get('/blog/tag/security', base_url=ACME)
    assert title.encode() in real.data


def test_a_search_of_percent_does_not_match_every_post(app, client, acme,
                                                       globex, user):
    from app.models.discussion import DiscussionGroup, Post

    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = DiscussionGroup.query.filter_by(org_id=acme.id).first()
        db.session.add(Post(org_id=acme.id, group_id=group.id,
                            title='Findable', body='b', created_by_id=user.id))
        db.session.commit()
        slug = group.slug

    login_as(client, user)
    wildcard = client.get(f'/discussions/{slug}?q=%25', base_url=ACME)
    assert b'Findable' not in wildcard.data

    real = client.get(f'/discussions/{slug}?q=Findable', base_url=ACME)
    assert b'Findable' in real.data


def test_a_term_containing_a_backslash_still_finds_its_row(app, client, acme,
                                                           globex, user):
    """The escape character has to be declared to the database as well as
    applied to the term. Without ESCAPE, PostgreSQL happens to default to a
    backslash and SQLite does not, so dropping it breaks one engine silently."""
    from app.models.discussion import DiscussionGroup, Post

    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = DiscussionGroup.query.filter_by(org_id=acme.id).first()
        db.session.add(Post(org_id=acme.id, group_id=group.id,
                            title=r'Windows C:\path notes', body='b',
                            created_by_id=user.id))
        db.session.commit()
        slug = group.slug

    login_as(client, user)
    found = client.get(f'/discussions/{slug}?q=C:%5Cpath', base_url=ACME)
    assert b'Windows C' in found.data


def test_a_tag_containing_a_backslash_still_matches(app, client, acme):
    title = _tagged(app, acme, r'a\b')

    assert title.encode() in client.get('/blog/tag/a%5Cb', base_url=ACME).data
    assert title.encode() not in client.get('/blog/tag/a%25b', base_url=ACME).data


def test_admin_search_treats_a_wildcard_as_text(app, client, acme, globex,
                                                platform_admin):
    login_as(client, platform_admin)

    wildcard = client.get('/admin/orgs?q=%25', base_url='http://example.test')
    assert b'Acme' not in wildcard.data

    real = client.get('/admin/orgs?q=Acme', base_url='http://example.test')
    assert b'Acme' in real.data
