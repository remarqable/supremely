"""Tenant resolution and safe-by-default scoping.

See blueprint/patterns/tenancy.md. Supremely runs with PUBLIC_TENANTS=True:
g.org is set for anonymous visitors too, so every query stays tenant-scoped;
content visibility is a model-layer concern.
"""

from contextlib import contextmanager
from urllib.parse import quote

from flask import abort, current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.extensions import db
from app.models.base import OrgScoped

# Paths that belong to the installation, not to any organization.
# /files is NOT here: uploads are tenant data and must resolve the org.
INSTALLATION_PREFIXES = ('/static/', '/setup', '/admin', '/auth/', '/launcher')
INSTALLATION_EXACT = ('/health', '/favicon.ico', '/tls-check')


def is_installation_path(path: str) -> bool:
    return path in INSTALLATION_EXACT or path.startswith(INSTALLATION_PREFIXES)


def init_tenant(app):
    @app.before_request
    def resolve_tenant():
        g.org = None
        g.membership = None
        g.pop('installed_plugins', None)    # tenant-derived: never reuse

        if is_installation_path(request.path):
            return

        host = _request_host()
        base = _base_domain()
        if host.endswith('.' + base):
            org = _from_subdomain()
            if org is None:
                abort(404)              # subdomain that maps to no organization
        elif host == base:
            org = _default_org()
        else:
            from app.models.domain import OrgDomain
            org = OrgDomain.resolve(host)
            if org is None:
                abort(404)              # foreign host with no active custom domain
        if org is None:
            return                      # bare domain, zero/multiple orgs: launcher/login

        if not org.is_active:
            abort(410, 'This organization has been deactivated')

        # Set g.org BEFORE any auth/membership decision: the tenant filter
        # keys off g.org, and anonymous reads must be scoped reads.
        g.org = org

        if current_user.is_authenticated:
            from app.models import Membership
            membership = Membership.get(current_user.id, org.id)
            # A suspended membership grants nothing: the user sees the org
            # exactly as an anonymous/outside visitor would.
            if membership is not None and membership.is_active:
                g.membership = membership

        if not app.config.get('PUBLIC_TENANTS') and g.membership is None:
            abort(404)                  # not 403: 403 confirms the org exists

    app.jinja_env.globals['org_url'] = org_url


def _request_host() -> str:
    host = request.host
    # IPv6 literals arrive bracketed ([::1]:8000); others just carry a port.
    host = (host[1:host.find(']')] if host.startswith('[')
            else host.split(':')[0])
    # Loopback IPs are the same machine as localhost. Without this, opening
    # http://127.0.0.1:8000 in dev resolves as a foreign host (custom-domain
    # lookup) and 404s while http://localhost:8000 works.
    if host in ('127.0.0.1', '::1') and _base_domain() == 'localhost':
        return 'localhost'
    return host


def _base_domain() -> str:
    return current_app.config['BASE_DOMAIN'].split(':')[0]


def _org_from_subdomain_of(host: str, base: str):
    """Organization named by <slug>.<base>, active or not, or None."""
    from app.models import Organization
    if not host.endswith('.' + base):
        return None
    slug = host[: -(len(base) + 1)]
    if not slug or '.' in slug or slug in Organization.RESERVED_SLUGS:
        return None
    return Organization.get_by_slug(slug)


def _sole_active_org():
    """The one active organization, or None when there are zero or many."""
    from app.models import Organization
    orgs = Organization.query.filter_by(is_active=True).limit(2).all()
    return orgs[0] if len(orgs) == 1 else None


def _from_subdomain():
    return _org_from_subdomain_of(_request_host(), _base_domain())


def _default_org():
    """Default-org mode: the bare domain answers for a sole organization.

    When the host IS the base domain and exactly one active organization
    exists, resolve to it. With a second organization the bare domain reverts
    to installation pages and subdomains take over.
    """
    if _request_host() != _base_domain():
        return None
    return _sole_active_org()


def org_for_host(host: str):
    """The ACTIVE organization a hostname serves, or None.

    The one place that maps an arbitrary hostname to an organization without
    a request context. Callers serving a request use org_for_request_host();
    this exists for decisions made about a host we were merely handed, such
    as whether to issue a TLS certificate for it.

    Unlike the resolution helpers above, this filters on is_active: a
    suspended organization serves nothing, so nothing should be provisioned
    on its behalf.
    """
    from app.models.domain import OrgDomain

    host = (host or '').strip().lower().split(':')[0].rstrip('.')
    if not host:
        return None
    base = _base_domain().lower()

    if host.endswith('.' + base):
        org = _org_from_subdomain_of(host, base)
    elif host == base:
        org = _sole_active_org()
    else:
        org = OrgDomain.resolve(host)

    return org if org is not None and org.is_active else None


def org_for_request_host():
    """Resolve the organization the current host serves, without aborting.
    For installation paths (like /auth) where resolve_tenant skips."""
    from app.models.domain import OrgDomain
    host = _request_host()
    base = _base_domain()
    if host.endswith('.' + base):
        return _from_subdomain()
    if host == base:
        return _default_org()
    return OrgDomain.resolve(host)


def org_url(org, path: str = '/') -> str:
    """Absolute URL for an organization, honoring default-org mode."""
    from app.models import Organization
    scheme = request.scheme if has_request_context() else 'http'
    host = request.host if has_request_context() else current_app.config['BASE_DOMAIN']
    port = host.split(':')[1] if ':' in host else None
    base = _base_domain()

    active = Organization.query.filter_by(is_active=True).limit(2).count()
    domain = base if active == 1 and org.is_active else f'{org.slug}.{base}'
    if port:
        domain = f'{domain}:{port}'
    return f'{scheme}://{domain}{quote(path)}'


# --- Safe-by-default scoping ------------------------------------------------

def _targets_org_scoped(statement) -> bool:
    """True if a bulk UPDATE/DELETE targets an OrgScoped model."""
    entity = getattr(statement, 'entity_description', None)
    if entity and isinstance(entity.get('entity'), type) \
            and issubclass(entity['entity'], OrgScoped):
        return True
    table = getattr(statement, 'table', None)
    if table is not None:
        mapped = getattr(table, '_annotations', {}).get('parententity', None)
        cls = getattr(mapped, 'class_', None)
        if isinstance(cls, type) and issubclass(cls, OrgScoped):
            return True
    return False


@event.listens_for(Session, 'do_orm_execute')
def _apply_tenant_filter(state):
    if state.session.info.get('unscoped'):
        return
    if not has_request_context():
        return                      # CLI, migrations, workers: use unscoped()

    # Bulk UPDATE/DELETE cannot be transparently scoped by loader criteria and
    # bypasses before_flush, so it must not be issued unscoped against an
    # OrgScoped model from a request. Fail loudly rather than cross tenants.
    if (state.is_update or state.is_delete) and not state.is_select \
            and _targets_org_scoped(state.statement):
        raise RuntimeError(
            'Bulk UPDATE/DELETE on an OrgScoped model must filter org_id '
            'explicitly (or run under unscoped()).')

    if not state.is_select or state.is_column_load or state.is_relationship_load:
        return

    org = getattr(g, 'org', None)
    if org is None:
        return

    org_id = org.id
    state.statement = state.statement.options(
        with_loader_criteria(OrgScoped, lambda cls: cls.org_id == org_id,
                             include_aliases=True)
    )


@event.listens_for(Session, 'before_flush')
def _stamp_org(session, _ctx, _instances):
    if not has_request_context():
        return
    org = getattr(g, 'org', None)
    for obj in session.new:
        if isinstance(obj, OrgScoped):
            if obj.org_id is None:
                if org is None:
                    raise RuntimeError(f'{type(obj).__name__} created without a tenant')
                obj.org_id = org.id
            elif org is not None and obj.org_id != org.id:
                raise RuntimeError('Refusing to write across tenants')
    # Updates too: the read filter makes cross-tenant rows unreachable, but
    # an object smuggled in via the identity map must still not be written.
    for obj in session.dirty:
        if isinstance(obj, OrgScoped) and org is not None \
                and obj.org_id is not None and obj.org_id != org.id:
            raise RuntimeError('Refusing to write across tenants')


@contextmanager
def unscoped():
    """Disable tenant filtering. Every use should be justified in review."""
    db.session.info['unscoped'] = True
    try:
        yield
    finally:
        db.session.info.pop('unscoped', None)
