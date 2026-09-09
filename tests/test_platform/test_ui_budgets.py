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
BUDGETS = {
    '/blog': (4, 3, 27),
    '/blog/hello': (5, 2, 17),      # 2xl: the article title, see the doc
    '/events': (4, 3, 23),
    '/members': (5, 2, 15),         # 2xl: the member's name on their card
    '/discussions/': (4, 2, 23),
}


@pytest.fixture
def shell(app, acme, globex, client):
    """One published article, so every listed page has something on it."""
    with app.test_request_context():
        g.org = acme
        item = Content(type='article', title='Hello', slug='hello',
                       body='Body.', org_id=acme.id, visibility='public',
                       fields={}, tags=[])
        item.save()
        item.publish()
    return client


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


# theme -> the same three budgets for the themed front page.
THEME_BUDGETS = {
    'origin': (4, 3, 18),
    'supremely': (8, 4, 74),    # the flagship, and the messiest: see the doc
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
