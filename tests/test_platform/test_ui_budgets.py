"""Budgets from blueprint/patterns/core/frontend.md, checked on real pages.

Counted on rendered HTML rather than on templates, because a screen is
assembled from four or five of them -- navbar, sidebar, main, rail -- and no
single file owns one. Only what a visitor actually receives is countable.

These are ratchets set at exactly what each page does today, so the suite is
green now and anything added fails. Lower a number when you have taken
something out. Raising one is a decision, not a fix: say in the commit
message what was added and why it earned its place.
"""
import re

import pytest
from flask import g

from app.models import Content

ACME = 'http://acme.example.test'

_SIZE_RE = re.compile(r'\btext-(\[[0-9]+px\]|xs|sm|base|lg|xl|[2-9]xl)\b')
_WEIGHT_RE = re.compile(r'\bfont-(thin|light|normal|medium|semibold|bold|black)\b')
# Tailwind's `.5` steps are 2px multiples: mt-1.5 is 6px, gap-2.5 is 10px.
# None divisible by 4.
_HALF_RE = re.compile(
    r'\b(?:gap|gap-[xy]|p|px|py|pt|pb|ps|pe|m|mx|my|mt|mb|ms|me|'
    r'space-[xy]|w|h|size)-[0-9]+\.5\b')

# Two surfaces, two type budgets. Four sizes is right for an app shell and
# wrong for a marketing page: a hero legitimately needs display sizes the
# shell never does. The no-bespoke-sizes rule applies to both.
#
# Built-in themes are in scope because they ship with the product and are
# what a theme author copies. An installed third-party theme is not: a theme
# is somebody's design, and imposing our scale on it would defeat theming.

# path -> (font sizes, font weights, off-grid spacing values)
# The spacing numbers went up by four on every page when the shared header
# landed (52f916e): its nav pill and dropdown use gap-1.5, py-1.5 and px-3.5.
# Raised rather than rounded, because rounding another author's just-merged
# component is a design decision that belongs to them -- but recorded here so
# it is a debt somebody took on, not a number that quietly drifted.
# The weight budget went up by one on three pages when attribution reached
# the shell: "Powered by Supremely" is on every page, the community included,
# and its link is medium weight so it reads as a link. The same partial
# every theme footer already carries, at the same weight.
BUDGETS = {
    '/blog': (4, 3, 31),
    '/blog/hello': (5, 3, 21),      # 2xl: the article title, see the doc
    '/events': (4, 3, 27),
    '/members': (5, 3, 19),         # 2xl: the member's name on their card
    '/discussions/': (4, 3, 27),
}


@pytest.fixture
def shell(app, acme, globex, client):
    """A community with something on every surface these pages draw.

    Conditional components are the ones that escape a budget: the pinned
    card and the notification badge both shipped a bespoke font size while
    the fixture had nothing pinned and nothing unread, so neither ever
    rendered here. Anything the rail can show, this fixture gives it.
    """
    from app.models.discussion import DiscussionGroup, Post

    with app.test_request_context():
        g.org = acme
        item = Content(type='article', title='Hello', slug='hello',
                       body='Body.', org_id=acme.id, visibility='public',
                       fields={}, tags=[])
        item.save()
        item.publish()

        group = (DiscussionGroup.query.order_by(DiscussionGroup.id).first()
                 or DiscussionGroup(org_id=acme.id, name='General',
                                    slug='general', visibility='public').save())
        pinned = Post(org_id=acme.id, group_id=group.id, title='Pinned thread',
                      body='Something worth keeping at the top.',
                      is_pinned=True)
        pinned.save()
    return client


# The same pages framed by the Supremely theme, which declines the shell
# ("community_nav": false in its theme.json) and draws community screens
# inside its own layout. Its marketing header and footer wrap an app screen,
# so each page costs one more size and one more weight than it does in the
# shell (the header's base-size nav links and its bold wordmark) and four
# more off-grid values (the header's and footer's spacing). Ratcheted at
# today's numbers, like everything else here.
SUPREMELY_BUDGETS = {
    '/blog': (5, 3, 35),
    '/blog/hello': (6, 3, 25),
    '/events': (5, 3, 31),
    '/members': (5, 3, 23),
    '/discussions/': (5, 3, 31),
}


@pytest.fixture
def supremely(shell, app, acme):
    with app.test_request_context():
        g.org = acme
        acme.theme = 'supremely'
        acme.save()
    return shell


@pytest.mark.parametrize('path', sorted(BUDGETS))
def test_a_page_stays_within_its_type_budget(shell, path):
    max_sizes, max_weights, _ = BUDGETS[path]
    html = shell.get(path, base_url=ACME).get_data(as_text=True)
    sizes = sorted(set(_SIZE_RE.findall(html)))
    weights = sorted(set(_WEIGHT_RE.findall(html)))
    assert len(sizes) <= max_sizes, f'{path}: {sizes}'
    assert len(weights) <= max_weights, f'{path}: {weights}'


@pytest.mark.parametrize('path', sorted(BUDGETS))
def test_a_page_uses_no_bespoke_font_size(shell, path):
    """A one-off px size is how a scale of four becomes a scale of nine."""
    html = shell.get(path, base_url=ACME).get_data(as_text=True)
    bespoke = sorted({s for s in _SIZE_RE.findall(html) if s.startswith('[')})
    assert not bespoke, f'{path}: {bespoke}'


@pytest.mark.parametrize('path', sorted(BUDGETS))
def test_a_page_adds_no_off_grid_spacing(shell, path):
    """Spacing divisible by 4. The half-steps counted here are pre-existing;
    the budget stops the number growing while they are worked off."""
    _, _, max_half = BUDGETS[path]
    html = shell.get(path, base_url=ACME).get_data(as_text=True)
    found = _HALF_RE.findall(html)
    assert len(found) <= max_half, (
        f'{path}: {len(found)} off-grid values, budget {max_half} '
        f'({sorted(set(found))})')


@pytest.mark.parametrize('path', sorted(SUPREMELY_BUDGETS))
def test_a_supremely_framed_page_stays_within_its_budget(supremely, path):
    max_sizes, max_weights, max_half = SUPREMELY_BUDGETS[path]
    html = supremely.get(path, base_url=ACME).get_data(as_text=True)
    sizes = sorted(set(_SIZE_RE.findall(html)))
    weights = sorted(set(_WEIGHT_RE.findall(html)))
    bespoke = sorted({s for s in sizes if s.startswith('[')})
    found = _HALF_RE.findall(html)
    assert len(sizes) <= max_sizes, f'{path}: {sizes}'
    assert len(weights) <= max_weights, f'{path}: {weights}'
    assert not bespoke, f'{path}: {bespoke}'
    assert len(found) <= max_half, (
        f'{path}: {len(found)} off-grid values, budget {max_half} '
        f'({sorted(set(found))})')


# theme -> the same three budgets for the themed front page.
THEME_BUDGETS = {
    'origin': (4, 3, 18),
    'supremely': (6, 4, 40),    # was (8, 4, 74) before the front page was rebuilt
    'midnight': (4, 3, 3),
    'trailhead': (5, 2, 1),
}


@pytest.mark.parametrize('theme', sorted(THEME_BUDGETS))
def test_a_built_in_theme_stays_within_its_budget(app, client, acme, globex,
                                                  theme):
    """The marketing side, one built-in theme at a time.

    Ratcheted at today's numbers like the shell. `supremely` carries most of
    the debt -- it is the default front page and the worst offender, which is
    the wrong way round for the theme others are copied from.
    """
    max_sizes, max_weights, max_half = THEME_BUDGETS[theme]
    with app.test_request_context():
        g.org = acme
        acme.theme = theme
        acme.save()
    html = client.get('/', base_url=ACME).get_data(as_text=True)
    sizes = sorted(set(_SIZE_RE.findall(html)))
    weights = sorted(set(_WEIGHT_RE.findall(html)))
    assert len(sizes) <= max_sizes, f'{theme}: {sizes}'
    assert len(weights) <= max_weights, f'{theme}: {weights}'
    assert len(_HALF_RE.findall(html)) <= max_half, (
        f'{theme}: {len(_HALF_RE.findall(html))} off-grid, budget {max_half}')


def test_reduced_motion_is_honoured_by_the_built_stylesheet():
    """Asserted on the built CSS, not the source: the source is not what a
    browser loads, and a stale app.css is exactly the failure worth catching.
    """
    from pathlib import Path
    assert 'prefers-reduced-motion' in Path('app/static/css/app.css').read_text()
