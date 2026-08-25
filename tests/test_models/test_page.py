import pytest
from flask import g

from app.extensions import db
from app.models import Page
from app.platform.errors import ValidationError


def make_page(app, org, **kwargs):
    defaults = dict(title='Story', slug='story', body='Hello **world**',
                    org_id=org.id)
    defaults.update(kwargs)
    with app.test_request_context():
        g.org = org
        page = Page(**defaults)
        page.save()
        db.session.refresh(page)
    return page


def test_create_and_publish(app, acme):
    page = make_page(app, acme)
    assert page.status == 'draft'
    assert not page.is_published

    with app.test_request_context():
        g.org = acme
        page.publish()
    assert page.is_published
    assert page.published_at is not None

    with app.test_request_context():
        g.org = acme
        page.unpublish()
    assert not page.is_published
    assert page.published_at is not None    # original publish date survives


def test_markdown_rendered_and_sanitized(app, acme):
    page = make_page(app, acme, body='# Hi\n\n<script>alert(1)</script>**bold**')
    html = page.html
    assert '<h1>' in html
    assert '<strong>bold</strong>' in html
    assert '<script>' not in html


def test_reserved_slug_rejected(app, acme):
    with pytest.raises(ValidationError, match='reserved'):
        make_page(app, acme, slug='manage')


def test_duplicate_slug_same_org_rejected(app, acme):
    make_page(app, acme)
    with pytest.raises(ValidationError, match='already exists'):
        make_page(app, acme, title='Story 2')


def test_same_slug_different_orgs_allowed(app, acme, globex):
    make_page(app, acme)
    other = make_page(app, globex)      # same 'about' slug, different tenant
    assert other.id is not None


def test_invalid_slug(app, acme):
    with pytest.raises(ValidationError):
        make_page(app, acme, slug='Has Space')
