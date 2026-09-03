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


def test_videos_are_labelled_videos_but_still_live_at_recordings(app):
    """The type shows as Video/Videos because "Recording" read as audio.

    Labels only: the slug and the archive URL are what published links and
    stored rows are made of, and both stay put. The singular is "Video" and
    not "Videos" because it renders in "New {name}" buttons, in per-item
    badges, and in the duplicate-slug validation error.
    """
    ct = CONTENT_TYPES['recording']
    assert (ct.singular, ct.plural) == ('Video', 'Videos')
    assert ct.slug == 'recording'
    assert ct.base == '/recordings'


def test_no_base_collisions(app):
    bases = [ct.base for ct in CONTENT_TYPES.values() if ct.base]
    assert len(bases) == len(set(bases))


def test_planned_types_are_valid_and_unregistered(app):
    validate_planned_types()
    assert len(COMING_SOON) >= 3


def test_a_video_asks_for_a_url_and_nothing_else(app):
    """Duration, speakers and a recorded-on date were removed: three things
    to type that nothing rendered. A value posted for one of them is not an
    error, it is simply not stored."""
    ct = get_content_type('recording')
    with pytest.raises(ValidationError):
        ct.clean_fields({})
    cleaned = ct.clean_fields({'video_url': 'https://example.com/v/1',
                               'duration_minutes': '57',
                               'speakers': 'Someone'})
    assert cleaned == {'video_url': 'https://example.com/v/1'}


def test_an_episode_asks_for_a_url_and_nothing_else(app):
    ct = get_content_type('episode')
    cleaned = ct.clean_fields({'audio_url': 'https://example.com/a/1',
                               'episode_number': '4',
                               'duration_minutes': '20'})
    assert cleaned == {'audio_url': 'https://example.com/a/1'}


def test_count_by_type_is_tenant_scoped(app, acme, globex):
    """count_by_type is a column-only query; the tenant filter must still
    apply (guards against a loader-criteria behavior change)."""
    from flask import g

    from app.models import Content

    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        counts = dict(Content.count_by_type())
    # Seeded content for ONE org only: 3 pages, 2 articles (one public, one
    # members-only to demonstrate gating), 1 event.
    assert counts == {'page': 3, 'article': 2, 'event': 1,
                      'announcement': 1, 'recording': 1, 'episode': 1,
                      'resource': 1}


def test_nav_groups_declared(app):
    groups = {slug: ct.group for slug, ct in CONTENT_TYPES.items()
              if ct.has_archive}
    assert groups['article'] == 'community'
    assert groups['announcement'] == 'community'
    assert groups['event'] == 'meet'
    assert {groups['recording'], groups['episode'], groups['resource']} == {'learn'}
    assert groups['definition'] == 'learn'          # plugin-declared
