"""The premade content-type library.

Core (content_types.register_core_types) defines the structural minimum:
page, article, event, link. This module is the library of common community
content types beyond that — the shelves a community picks from. Today every
library type registers for every organization; per-org enablement and
"community type" presets (a named selection of these at org setup) build on
top of this list.

Types that need platform features we don't have yet (file/select field
types, child content) are declared in COMING_SOON: visible in Manage as
placeholders, never registered, never routable.
"""

from dataclasses import dataclass

from app.platform.content_types import (CONTENT_TYPES, ContentType, FieldSpec,
                                        register_content_type)


def register_library_types() -> None:
    """Common community types buildable with today's field types."""
    if 'recording' in CONTENT_TYPES:
        return
    register_content_type(ContentType(
        slug='recording', singular='Recording', plural='Recordings',
        description='Video recordings: webinars, talks, member deep dives.',
        base='/recordings',
        fields=(
            FieldSpec(key='video_url', type='url', label='Video URL',
                      required=True,
                      help='Where the video is hosted (YouTube, Vimeo, ...).'),
            FieldSpec(key='duration_minutes', type='number',
                      label='Duration (minutes)'),
            FieldSpec(key='speakers', type='string', label='Speakers'),
            FieldSpec(key='recorded_on', type='date', label='Recorded on'),
        ),
    ))
    register_content_type(ContentType(
        slug='episode', singular='Episode', plural='Podcast',
        description='Podcast episodes with an audio link and episode number.',
        base='/podcast',
        fields=(
            FieldSpec(key='audio_url', type='url', label='Audio URL',
                      required=True),
            FieldSpec(key='episode_number', type='number', label='Episode #'),
            FieldSpec(key='duration_minutes', type='number',
                      label='Duration (minutes)'),
        ),
    ))
    register_content_type(ContentType(
        slug='resource', singular='Resource', plural='Resources',
        description='Reports, guides, and documents members can download.',
        base='/resources',
        fields=(
            FieldSpec(key='resource_url', type='url', label='Resource URL',
                      required=True,
                      help='Link to the document — an upload from Media, '
                           'or an external URL.'),
            FieldSpec(key='kind', type='string', label='Kind',
                      help='Report, guide, whitepaper, template, ...'),
        ),
    ))


@dataclass(frozen=True)
class PlannedType:
    """A library entry we intend to ship but cannot build well yet. Shown in
    Manage as a placeholder; the `needs` note records what unblocks it."""
    slug: str
    singular: str
    plural: str
    description: str
    needs: str


COMING_SOON: tuple[PlannedType, ...] = (
    PlannedType(
        slug='job', singular='Job', plural='Jobs',
        description='A job board: openings with company, location, and '
                    'an application link.',
        needs='select field type (employment type, remote/on-site)'),
    PlannedType(
        slug='course', singular='Course', plural='Courses',
        description='Structured learning: a course made of ordered lessons.',
        needs='child content (lessons that belong to a course)'),
    PlannedType(
        slug='opportunity', singular='Opportunity', plural='Opportunities',
        description='Member-only deals and offers with a status and a '
                    'deadline.',
        needs='select field type (status) and deadline-driven visibility'),
    PlannedType(
        slug='gallery', singular='Gallery', plural='Galleries',
        description='Photo sets from events and meetups.',
        needs='multi-image field type'),
    PlannedType(
        slug='recipe', singular='Recipe', plural='Recipes',
        description='The classic vertical: ingredients, steps, and times.',
        needs='repeating list field type (ingredients)'),
)


def validate_planned_types() -> None:
    """Planned slugs must stay valid and free: shipping one later must never
    collide with a registered type. Called from tests."""
    seen = set()
    for planned in COMING_SOON:
        if planned.slug in CONTENT_TYPES:
            raise ValueError(
                f'{planned.slug} is registered; remove it from COMING_SOON')
        if planned.slug in seen:
            raise ValueError(f'Duplicate planned slug: {planned.slug}')
        seen.add(planned.slug)
        if not (planned.singular and planned.plural and planned.needs):
            raise ValueError(f'Planned type {planned.slug} is incomplete')
