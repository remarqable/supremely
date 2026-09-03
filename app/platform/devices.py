"""Device-aware rendering: one page, an optional mobile version of it.

See blueprint/patterns/core/mobile-navigation.md. The rule that keeps this
from becoming two applications:

    Responsive CSS is the default. A dedicated mobile template exists only
    where a phone needs a different layout that `sm:`/`md:` prefixes cannot
    express. When one is absent, the ordinary template renders.

So a surface costs nothing until someone decides it needs its own mobile
version, and no template, theme or content type is ever obliged to provide
one.

A mobile template is a sibling of the one it replaces, under `mobile/`:

    manage/media.html          ->  manage/mobile/media.html
    community/single.html      ->  community/mobile/single.html
    themes/origin/single.html  ->  themes/origin/mobile/single.html

That differs from sparQ, which keys off a literal `/desktop/` segment and
therefore requires every template to live in a `desktop/` folder before any
of them can have a mobile variant. Naming the exception rather than the rule
means nothing has to move, and the file that exists today stays the file
that renders.

Three smaller departures from the blueprint's sketch, for the same reason
of less ceremony for the same behaviour:

    one module, not a devices/ package, matching the flat shape of the rest
    of app/platform/;

    the mobile shell layout is layouts/mobile/community.html rather than a
    separately named layouts/mobile_base.html, so it follows the sibling
    rule like everything else;

    detection reads the request rather than taking a User-Agent string,
    because every caller is inside one. Tests exercise it through
    test_request_context, which costs a line and keeps one code path.
"""

import re

from flask import has_request_context, request, session

# Enough of the User-Agent zoo to cover phones and tablets. Deliberately
# coarse: this picks a layout, never an access decision, so being wrong
# costs a reader the wrong arrangement of the same content and nothing more.
MOBILE_RE = re.compile(
    r'Mobile|Android|webOS|iPhone|iPad|iPod|BlackBerry|Windows Phone|'
    r'Opera Mini|IEMobile', re.IGNORECASE)

DEVICE_TYPES = ('mobile', 'desktop')
# ?device=auto puts a reader back on automatic detection. Without a way
# back, a single link with ?device= on it would pin someone to the wrong
# layout for the life of their session with nothing they could do about it.
AUTO = 'auto'
OVERRIDE_KEY = 'device_override'


def detect_device() -> str:
    """The device the User-Agent claims to be."""
    if not has_request_context():
        return 'desktop'
    return 'mobile' if MOBILE_RE.search(
        request.headers.get('User-Agent', '')) else 'desktop'


def device_type() -> str:
    """The device to render for.

    A `?device=` parameter wins and is remembered, so a developer can hold a
    mobile layout open on a desktop browser, and a reader who prefers the
    other one can stay there. Failing that, the User-Agent decides, fresh
    each request.
    """
    if not has_request_context():
        return 'desktop'
    asked = request.args.get('device')
    if asked in DEVICE_TYPES:
        set_device_type(asked)
        return asked
    if asked == AUTO:
        clear_device_type()
        return detect_device()
    chosen = session.get(OVERRIDE_KEY)
    if chosen in DEVICE_TYPES:
        return chosen
    return detect_device()


def is_mobile() -> bool:
    return device_type() == 'mobile'


def set_device_type(chosen: str) -> None:
    """Remember a reader's choice of layout."""
    if chosen not in DEVICE_TYPES:
        raise ValueError(f'Unknown device type: {chosen!r}')
    session[OVERRIDE_KEY] = chosen


def clear_device_type() -> None:
    """Forget the choice and go back to what the User-Agent says."""
    session.pop(OVERRIDE_KEY, None)


def mobile_variant(name: str) -> str:
    """The mobile sibling of a template name.

    'manage/media.html' -> 'manage/mobile/media.html'
    'media.html'        -> 'mobile/media.html'
    """
    head, _, tail = name.rpartition('/')
    return f'{head}/mobile/{tail}' if head else f'mobile/{tail}'


def device_candidates(names: list[str]) -> list[str]:
    """Expand a candidate list with mobile siblings, most specific first.

    Each name keeps its position and gains its mobile variant immediately
    ahead of it, so a mobile template only ever displaces the exact template
    it is the mobile version of, never one further down the chain that a
    theme or the community shell would otherwise have won with.
    """
    if not is_mobile():
        return names
    expanded = []
    for name in names:
        expanded.append(mobile_variant(name))
        expanded.append(name)
    return list(dict.fromkeys(expanded))


def render_device_template(*names: str, **context) -> str:
    """render_template, with the mobile version preferred where one exists.

    A drop-in replacement: pass one template name, or several to try in
    order, and the first that exists renders, mobile siblings first.
    """
    return render_device(list(names), **context)


def render_device(names: list[str], **context) -> str:
    """As render_device_template, for a list that is already assembled."""
    from flask import render_template
    return render_template(device_candidates(names), **context)
