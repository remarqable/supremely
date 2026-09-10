"""Roles and permissions. Three roles, flat permission sets.

Moderation permissions are granted to admin/owner; checks name permissions,
never roles, so adding a moderator role later stays cheap.
"""

from functools import wraps
from typing import TYPE_CHECKING, NamedTuple

from flask import abort, g, has_request_context, redirect, request, url_for
from flask_login import current_user

from app.platform.logger import log_refusal

if TYPE_CHECKING:
    from app.models.tier import Tier

# Visibility levels an object or section may carry. Access lives on the
# OBJECT (Content.visibility, DiscussionGroup.visibility) and is enforced
# server-side before rendering — never by a theme.
#
# These two are the base vocabulary and it stays exactly two. Membership
# tiers extend the vocabulary alongside it rather than joining it, as
# `tier:<slug>` values (see TIER_PREFIX): a tier belongs to one
# organization, so the set of legal values is per-tenant and cannot be a
# module constant. Upload.other_visibility also unpacks this tuple into
# exactly two names on purpose, to fail loudly if a third ever arrives.
VISIBILITY_LEVELS = ('public', 'members')

# A visibility value naming the lowest tier that may read the thing. Anyone
# at or above that tier passes; see can_read.
TIER_PREFIX = 'tier:'


def tier_slug(visibility: str) -> str | None:
    """The tier a visibility value requires, or None for the base levels."""
    value = visibility or ''
    if not value.startswith(TIER_PREFIX):
        return None
    return value[len(TIER_PREFIX):] or None


def visibility_value(tier: 'Tier') -> str:
    """The visibility value that requires this tier. One place, so a caller
    never spells the prefix out."""
    return f'{TIER_PREFIX}{tier.slug}'


def visibility_choices() -> list[tuple[str, str]]:
    """(value, label) pairs the current organization may choose from.

    The two base levels, then one entry per **active** tier in ladder
    order. The label for a base level is the bare value, which a caller
    turns into catalogue text; a tier's label is its own name, because an
    organization named it and it is not a translatable string.

    This is the picker, not the validator: a retired tier is not offered as
    a new choice but content already gated to one is still legal. See
    visibility_is_valid.
    """
    # From the memo, not a fresh query: this runs once per picker, and a
    # page of content types draws one per type.
    choices = [(level, visibility_label(level)) for level in VISIBILITY_LEVELS]
    return choices + [(f'{TIER_PREFIX}{slug}', rung.name)
                      for slug, rung in tiers_by_slug().items()
                      if rung.is_active]


def visibility_options(current: str | None,
                       extra: list | None = None) -> list[tuple[str, str, bool]]:
    """(value, label, selected) for a visibility picker.

    The stored value is kept even when the picker would not otherwise
    offer it, which is how an item gated to a retired tier holds on to it:
    without that the select comes up with nothing chosen, the browser
    posts the first option, and the next save makes it public.
    """
    chosen = current or VISIBILITY_LEVELS[0]
    options = list(extra or []) + visibility_choices()
    if chosen not in [value for value, _ in options]:
        options.append((chosen, visibility_label(chosen)))
    return [(value, label, value == chosen) for value, label in options]


class Rung(NamedTuple):
    """One rung of the ladder, as the memo keeps it."""
    rank: int
    name: str
    is_active: bool


def _read_ladder(org_id: int) -> dict[str, Rung]:
    """Every tier of one organization, bottom first, as plain values.

    Plain values rather than model instances on purpose. Instances expire
    on the first commit or rollback in the request, and the memo would
    then cost one refresh per rung exactly where it was meant to save
    thirty.

    org_id is named rather than left to the session filter, because this
    has to be right whether or not that filter is standing: read inside an
    unscoped() block, an unfiltered query would cache every organization's
    tiers under this one's key and answer with them for the rest of the
    request.
    """
    from app.models.tier import Tier
    from app.platform.tenant import unscoped
    with unscoped():
        rows = (Tier.query.filter_by(org_id=org_id)
                .order_by(Tier.rank, Tier.id).all())
    return {tier.slug: Rung(tier.rank, tier.name, tier.is_active)
            for tier in rows}


def tiers_by_slug() -> dict[str, Rung]:
    """{slug: Rung} for the organization in force, retired ones included.

    Memoized per request, and the one place the ladder is read. can_read
    runs once per item and a listing draws thirty of them; the label
    helper runs once per option in every picker, and a page of content
    types draws one picker per type. Read straight from the database,
    either of those is dozens of identical selects against a table with a
    handful of rows in it.

    Cleared with the other per-request memos in _reset_request_state,
    because a memo that outlives its request is how one organization's
    answers reach another's page. Empty off-tenant, where there is no
    organization to answer for.
    """
    from app.platform.tenant import current_org_id
    org_id = current_org_id()
    if org_id is None:
        return {}
    if not has_request_context():
        return _read_ladder(org_id)
    cache = g.setdefault('_tier_ranks', {})
    if org_id not in cache:
        cache[org_id] = _read_ladder(org_id)
    return cache[org_id]


def tier_ranks() -> dict[str, int]:
    """{slug: rank}, from the one memo above."""
    return {slug: rung.rank for slug, rung in tiers_by_slug().items()}


def forget_tier_ranks() -> None:
    """Drop the memo. Every mutation of the ladder calls this, because the
    console changes one and then renders the page it changed it on, inside
    one request. Retiring included: the memo carries whether a rung is
    active, which is what decides whether a picker offers it."""
    if has_request_context():
        g.pop('_tier_ranks', None)


def visibility_is_valid(visibility: str, org_id: int | None = None) -> bool:
    """Whether a stored visibility value is one this organization knows.

    Wider than visibility_choices on purpose, in the one way that matters:
    a retired tier still validates. Retiring a tier is meant to stop it
    being offered, not to make every item already gated to it unsaveable,
    which is what a validator built from the picker would do the moment
    anybody edited the title.

    Reads the organization in force rather than g.org, so a job running
    under org_scope validates against that organization's tiers instead of
    finding none and refusing every tier-gated save. A caller that knows
    which organization the row belongs to says so with `org_id`: a model
    validating itself knows that from the row, and should not have to
    depend on what the surrounding request happened to resolve.
    """
    from app.models.tier import Tier
    from app.platform.tenant import current_org_id, unscoped
    if visibility in VISIBILITY_LEVELS:
        return True
    slug = tier_slug(visibility)
    if slug is None:
        return False
    in_force = current_org_id()
    if org_id is None or org_id == in_force:
        # The ordinary case, and the memo already holds the answer.
        return slug in tier_ranks() if in_force is not None else False
    # Only a row belonging to some other organization needs the hatch, and
    # only to read: writing it there is refused by the tenant stamp.
    with unscoped():
        return Tier.query.filter_by(org_id=org_id, slug=slug).first() is not None


def visibility_label(visibility: str) -> str:
    """What to call a stored visibility value on screen.

    A base level is a catalogue key; a tier is named by the organization
    that made it, so its own name is the label. Templates built the key by
    hand before tiers, and a value like `tier:pro` turned that into a
    missing catalogue entry printed on the page.
    """
    from app.platform.i18n import t
    slug = tier_slug(visibility)
    if slug is None:
        return t(f'manage.visibility_{visibility}')
    rung = tiers_by_slug().get(slug)
    return rung.name if rung else visibility


def tier_name(visibility: str) -> str | None:
    """The name of the tier a visibility value requires, or None when it
    requires no tier. What the gate page says would open it."""
    slug = tier_slug(visibility or '')
    rung = tiers_by_slug().get(slug) if slug else None
    return rung.name if rung else None


def can_read(visibility: str) -> bool:
    """Whether the current visitor may read something carrying this value.

    The single read rule. Eleven callsites asked this question in their own
    words before tiers existed, which is eleven chances for one of them to
    drift from the rest.

    In order: public is public. Outside a request there is no visitor to
    answer for, so no. A platform admin sees every organization. A visitor
    who is not a member gets nothing further. A member who may write or
    moderate content reads all of it whatever tier they hold, which is what
    an administrator checking their own site expects; it is spelled as a
    permission rather than a role so a moderator role added later inherits
    it. Then `members` means any member. Anything else names a tier, and
    the member's own tier has to reach it.

    A tier value that resolves to nothing reads False. Tiers are retired
    rather than deleted, so this should be unreachable and means a
    hand-edited row; hiding content is the safe direction to fail in.
    """
    if visibility == VISIBILITY_LEVELS[0]:
        return True
    if not has_request_context():
        return False            # no visitor to answer for: fail closed
    if current_user.is_authenticated and current_user.is_platform_admin:
        return True
    membership = getattr(g, 'membership', None)
    if membership is None:
        return False
    if can('content.write') or can('content.moderate'):
        return True
    if visibility == VISIBILITY_LEVELS[1]:
        return True
    slug = tier_slug(visibility)
    if slug is None:
        return False            # not a level this application knows
    required = tiers_by_slug().get(slug)
    required = required.rank if required else None
    held = membership.tier
    return (required is not None and held is not None
            and held.rank >= required)


def readable_visibilities() -> list[str] | None:
    """Every visibility value the current visitor may read, or None when
    they may read anything.

    The SQL half of can_read, for listings that filter rows out rather than
    teasing them. None rather than "all the values" because an
    administrator reads content gated to a tier that was retired years ago,
    and no enumeration of today's tiers would include it.
    """
    public = [VISIBILITY_LEVELS[0]]
    if not has_request_context():
        return public
    if current_user.is_authenticated and current_user.is_platform_admin:
        return None
    membership = getattr(g, 'membership', None)
    if membership is None:
        return public
    if can('content.write') or can('content.moderate'):
        return None
    held = membership.tier
    # Retired tiers included: a member who holds one, or who sits above
    # one, still reads what it gates. Leaving them out is what makes a
    # listing and the page it links to disagree.
    reachable = [f'{TIER_PREFIX}{slug}' for slug, rank in tier_ranks().items()
                 if held is not None and rank <= held.rank]
    return [*VISIBILITY_LEVELS, *reachable]


ROLE_PERMISSIONS = {
    'owner': {
        'read', 'discuss', 'content.write', 'content.moderate',
        'members.manage', 'org.settings', 'theme.manage', 'plugins.manage',
        'org.delete', 'ownership.transfer',
    },
    'admin': {
        'read', 'discuss', 'content.write', 'content.moderate',
        'members.manage', 'org.settings', 'theme.manage', 'plugins.manage',
    },
    'member': {'read', 'discuss'},
}


def grants_more_than(role: str, than: str) -> bool:
    """True when `role` carries a permission that `than` does not."""
    return bool(ROLE_PERMISSIONS.get(role, set())
                - ROLE_PERMISSIONS.get(than, set()))


def can(permission: str) -> bool:
    membership = getattr(g, 'membership', None)
    return bool(membership) and permission in ROLE_PERMISSIONS.get(membership.role, set())


def is_org_member() -> bool:
    return getattr(g, 'membership', None) is not None


def is_member_or_platform_admin() -> bool:
    """Sees this organization from the inside: an active member, or a
    platform admin. Spelled out at fifteen callsites before this existed,
    which is fourteen chances for one copy to drift."""
    return is_org_member() or (current_user.is_authenticated
                               and current_user.is_platform_admin)


def can_view(obj) -> bool:
    """Can the current visitor see this object? The single vocabulary for
    read access — delegates to the object's own policy. Rendering happens
    only after this says yes; themes never decide access."""
    if not has_request_context():
        return False            # no visitor to answer for: fail closed
    org = getattr(g, 'org', None)
    obj_org = getattr(obj, 'org_id', None)
    if obj_org is not None and (org is None or obj_org != org.id):
        # The loader filter already keeps other tenants' rows out of reach.
        # This is the second line, for anything handed in directly.
        return False
    if hasattr(obj, 'visible_to_current_visitor'):
        return obj.visible_to_current_visitor()
    if hasattr(obj, 'readable_by_current_visitor'):
        return obj.readable_by_current_visitor()
    raise TypeError(f'{type(obj).__name__} has no visibility policy')


def require(permission: str):
    """Route decorator: 403 unless the current member holds the permission."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.path))
            if not can(permission):
                log_refusal('permission_denied', permission=permission)
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def org_required(f):
    """Route decorator: 404 unless the request resolved to an organization."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if getattr(g, 'org', None) is None:
            abort(404)
        return f(*args, **kwargs)
    return wrapped


def platform_admin_required(f):
    """Route decorator for /admin: requires the installation-level privilege."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        if not current_user.is_platform_admin:
            # Logged as the denial it is, even though the visitor is told
            # the page does not exist.
            log_refusal('platform_admin_denied')
            abort(404)          # do not confirm /admin exists to non-admins
        return f(*args, **kwargs)
    return wrapped
