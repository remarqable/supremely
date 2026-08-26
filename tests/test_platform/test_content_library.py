"""The premade content-type library: registration and planned placeholders."""

import pytest

from app.platform.content_library import COMING_SOON, validate_planned_types
from app.platform.content_types import CONTENT_TYPES, get_content_type
from app.platform.errors import ValidationError


def test_library_types_registered(app):
    for slug, base in (('recording', '/recordings'), ('episode', '/podcast'),
                       ('resource', '/resources'),
                       ('announcement', '/announcements')):
        ct = CONTENT_TYPES[slug]
        assert ct.has_archive
        assert ct.base == base
        assert ct.plugin is None


def test_no_base_collisions(app):
    bases = [ct.base for ct in CONTENT_TYPES.values() if ct.base]
    assert len(bases) == len(set(bases))


def test_planned_types_are_valid_and_unregistered(app):
    validate_planned_types()
    assert len(COMING_SOON) >= 3


def test_recording_requires_video_url(app):
    ct = get_content_type('recording')
    with pytest.raises(ValidationError):
        ct.clean_fields({'speakers': 'Someone'})
    cleaned = ct.clean_fields({'video_url': 'https://example.com/v/1',
                               'duration_minutes': '57',
                               'recorded_on': '2026-03-18'})
    assert cleaned == {'video_url': 'https://example.com/v/1',
                       'duration_minutes': 57,
                       'recorded_on': '2026-03-18'}


def test_count_by_type_is_tenant_scoped(app, acme, globex):
    """count_by_type is a column-only query; the tenant filter must still
    apply (guards against a loader-criteria behavior change)."""
    from flask import g

    from app.models import Content

    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        counts = dict(Content.count_by_type())
    # Seeded content for ONE org only: 3 pages, 1 article, 1 event.
    assert counts == {'page': 3, 'article': 1, 'event': 1,
                      'announcement': 1}


def test_nav_groups_declared(app):
    groups = {slug: ct.group for slug, ct in CONTENT_TYPES.items()
              if ct.has_archive}
    assert groups['article'] == 'community'
    assert groups['announcement'] == 'community'
    assert groups['event'] == 'meet'
    assert {groups['recording'], groups['episode'], groups['resource']} == {'learn'}
    assert groups['definition'] == 'learn'          # plugin-declared
