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

from app.platform.content_types import (
    CONTENT_TYPES,
    ContentType,
    FieldSpec,
    register_content_type,
)


def register_library_types() -> None:
    """Common community types buildable with today's field types."""
    if 'recording' in CONTENT_TYPES:
        return
    register_content_type(ContentType(
        # Labels only: the slug stays 'recording' and the archive stays at
        # /recordings, so no published link breaks. "Recording" read as
        # audio to people who had not been told otherwise.
        slug='recording', singular='Video', plural='Videos',
        description='Talks, webinars and member deep dives, linked from '
                    'wherever the video is hosted.',
        base='/recordings', group='learn', icon='video',
        # One field. Duration, speakers and a recorded-on date were three
        # more things to type for something the video page already shows,
        # and nothing rendered them.
        fields=(
            FieldSpec(key='video_url', type='url', label='Video URL',
                      required=True,
                      help='Where the video is hosted (YouTube, Vimeo, ...).'),
        ),
    ))
    register_content_type(ContentType(
        slug='episode', singular='Episode', plural='Podcast',
        description='Podcast episodes, linked from wherever the audio is '
                    'hosted.',
        base='/podcast', group='learn', icon='podcast',
        fields=(
            FieldSpec(key='audio_url', type='url', label='Audio URL',
                      required=True),
        ),
    ))
    register_content_type(ContentType(
        slug='announcement', singular='Announcement', plural='Announcements',
        description='Official updates from the team.',
        base='/announcements', icon='announcement',
    ))
    register_content_type(ContentType(
        slug='team_member', singular='Team member', plural='Team',
        description='The people behind the organization: name, role, photo, '
                    'and a short bio.',
        base='/team', group='meet',
        # A roster is site furniture, not community activity: the archive
        # presents through the theme like a brochure page.
        presentation='site',
        fields=(
            FieldSpec(key='role', type='string', label='Role',
                      help='Founder, Designer, Community Manager, ...'),
        ),
    ))
    register_content_type(ContentType(
        slug='resource', singular='Resource', plural='Resources',
        description='Reports, guides, and documents members can download.',
        base='/resources', group='learn',
        fields=(
            FieldSpec(key='resource_url', type='url', label='Resource URL',
                      required=True,
                      help='Link to the document — an upload from Media, '
                           'or an external URL.'),
            FieldSpec(key='kind', type='string', label='Kind',
                      help='Report, guide, whitepaper, template, ...'),
        ),
    ))


    register_content_type(ContentType(
        slug='recipe', singular='Recipe', plural='Recipes',
        description='The classic vertical: ingredients, steps and times.',
        base='/recipes', group='learn', icon='document',
        fields=(
            FieldSpec(key='servings', type='number', label='Servings',
                      in_summary=True),
            FieldSpec(key='prep_time', type='datetime', label='Prep starts',
                      help='Optional. Useful for a class or a cook-along.'),
            FieldSpec(key='cook_time', type='datetime', label='Cook starts'),
            # One row per ingredient rather than a block of text, so a theme
            # can lay them out and a reader can tick them off.
            FieldSpec(key='ingredients', type='list', label='Ingredients',
                      of=(FieldSpec(key='amount', type='string',
                                    label='Amount'),
                          FieldSpec(key='item', type='string', label='Item',
                                    required=True))),
            FieldSpec(key='steps', type='list', label='Method',
                      of=(FieldSpec(key='step', type='text', label='Step',
                                    required=True),)),
        ),
    ))
    register_content_type(ContentType(
        slug='job', singular='Job', plural='Jobs',
        description='A job board: openings with company, location and an '
                    'application link.',
        base='/jobs', group='community', icon='document',
        fields=(
            FieldSpec(key='company', type='string', label='Company',
                      in_summary=True),
            FieldSpec(key='location', type='string', label='Location',
                      in_summary=True),
            FieldSpec(key='employment', type='select', label='Employment',
                      in_summary=True,
                      choices=(('full_time', 'Full time'),
                               ('part_time', 'Part time'),
                               ('contract', 'Contract'),
                               ('internship', 'Internship'))),
            FieldSpec(key='workplace', type='select', label='Workplace',
                      choices=(('on_site', 'On site'),
                               ('hybrid', 'Hybrid'),
                               ('remote', 'Remote'))),
            FieldSpec(key='apply_url', type='url', label='Application link',
                      required=True),
        ),
    ))
    register_content_type(ContentType(
        slug='opportunity', singular='Opportunity', plural='Opportunities',
        description='Deals and offers with a status and a deadline.',
        base='/opportunities', group='community', icon='document',
        fields=(
            FieldSpec(key='status', type='select', label='Status',
                      in_summary=True,
                      choices=(('open', 'Open'),
                               ('closing_soon', 'Closing soon'),
                               ('closed', 'Closed'))),
            FieldSpec(key='deadline', type='datetime', label='Deadline',
                      in_summary=True),
            FieldSpec(key='claim_url', type='url', label='How to claim'),
        ),
    ))
    register_content_type(ContentType(
        slug='gallery', singular='Gallery', plural='Galleries',
        description='Photo sets from events and meetups.',
        base='/galleries', group='meet', icon='document',
        fields=(
            FieldSpec(key='photos', type='list', label='Photos',
                      of=(FieldSpec(key='image', type='image', label='Photo',
                                    required=True),
                          FieldSpec(key='caption', type='string',
                                    label='Caption'))),
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
        slug='course', singular='Course', plural='Courses',
        description='Structured learning: a course made of ordered lessons.',
        needs='child content (lessons that belong to a course)'),
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
