"""Roles and permissions. Three roles, flat permission sets.

Moderation permissions are granted to admin/owner; checks name permissions,
never roles, so adding a moderator role later stays cheap.
"""

from functools import wraps

from flask import abort, g, has_request_context, redirect, request, url_for
from flask_login import current_user

from app.platform.logger import log_refusal

# Visibility levels an object or section may carry. Access lives on the
# OBJECT (Content.visibility, DiscussionGroup.visibility) and is enforced
# server-side before rendering — never by a theme. 'restricted' is reserved
# for future group/paid access; do not implement semantics for it yet.
VISIBILITY_LEVELS = ('public', 'members')


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
