"""Roles and permissions. Three roles, flat permission sets.

Moderation permissions are granted to admin/owner; checks name permissions,
never roles, so adding a moderator role later stays cheap.
"""

from functools import wraps

from flask import abort, g, redirect, request, url_for
from flask_login import current_user

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


def can(permission: str) -> bool:
    membership = getattr(g, 'membership', None)
    return bool(membership) and permission in ROLE_PERMISSIONS.get(membership.role, set())


def is_org_member() -> bool:
    return getattr(g, 'membership', None) is not None


def require(permission: str):
    """Route decorator: 403 unless the current member holds the permission."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.path))
            if not can(permission):
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
            abort(404)          # do not confirm /admin exists to non-admins
        return f(*args, **kwargs)
    return wrapped
