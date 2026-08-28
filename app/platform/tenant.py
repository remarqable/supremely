"""Tenant resolution and safe-by-default scoping.

See blueprint/patterns/tenancy.md. Supremely runs with PUBLIC_TENANTS=True:
g.org is set for anonymous visitors too, so every query stays tenant-scoped;
content visibility is a model-layer concern.

Scoping follows the organization in force, not the request. A request sets
it by resolving the host; the job worker sets it with org_scope() from the
organization the job was queued for. Only work with no organization at all,
the command line and migrations, runs unfiltered.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote

from flask import abort, current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, with_loader_criteria

from app.extensions import db
from app.models.base import OrgScoped
from app.platform.errors import TenantViolation
from app.platform.logger import log_refusal

# Paths that belong to the installation, not to any organization.
# /files is NOT here: uploads are tenant data and must resolve the org.
# Every prefix ends in a slash so it cannot match a longer word: '/admin'
# used to swallow '/adminfoo', which a tenant can legitimately publish.
INSTALLATION_PREFIXES = ('/static/', '/setup/', '/admin/', '/auth/',
                         '/launcher/')
INSTALLATION_EXACT = ('/health', '/favicon.ico', '/tls-check',
                      '/setup', '/admin', '/launcher')


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
    # Host names are case-insensitive and may carry a trailing root dot, so
    # normalise before comparing. org_for_host already did; this did not,
    # so the two disagreed about which tenant serves a request.
    raw = request.host.strip().lower()
    # IPv6 literals arrive bracketed ([::1]:8000); others just carry a port.
    if raw.startswith('['):
        end = raw.find(']')
        if end == -1:
            return ''               # malformed: resolve no tenant
        host = raw[1:end]
    else:
        host = raw.split(':')[0]
    host = host.rstrip('.')
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


# The organization a piece of work belongs to when there is no request to
# read it from. A job records the tenant it was queued for; this is how
# that record becomes the same guard a request gets.
_ambient_org: ContextVar = ContextVar('ambient_org_id', default=None)


@contextmanager
def org_scope(org_id):
    """Run work as though a request had resolved this organization.

    The filter used to key off the request, so everything outside one ran
    with no filter at all: the worker read and wrote across every tenant,
    and the only thing stopping it was that no handler happened to take an
    identifier from a visitor. Pass None for work that genuinely spans
    tenants, which then behaves as it always did.
    """
    token = _ambient_org.set(org_id)
    try:
        yield
    finally:
        _ambient_org.reset(token)


def current_org_id():
    """The tenant in force, from the request or from org_scope().

    Reads the identity rather than the attribute. This runs inside the
    query listener, and an expired instance would answer org.id by issuing
    a refresh, which enters the listener again and does not come back.
    """
    if has_request_context():
        org = getattr(g, 'org', None)
        if org is not None:
            identity = sa_inspect(org).identity
            return identity[0] if identity else org.id
    # Falls through rather than answering None for a request. A handler
    # that pushes its own request context, to build an absolute link or
    # render a template, has no resolved organization in it, and answering
    # from the request alone would throw away the one the job carries.
    return _ambient_org.get()


@event.listens_for(Session, 'do_orm_execute')
def _apply_tenant_filter(state):
    if state.session.info.get('unscoped'):
        return

    org_id = current_org_id()
    scoped = has_request_context() or org_id is not None
    if not scoped:
        return                      # CLI and migrations: use unscoped()

    # Bulk UPDATE/DELETE cannot be transparently scoped by loader criteria and
    # bypasses before_flush, so it must not be issued unscoped against an
    # OrgScoped model from a request. Fail loudly rather than cross tenants.
    if (state.is_update or state.is_delete) and not state.is_select \
            and _targets_org_scoped(state.statement):
        log_refusal('tenant_bulk_statement_refused')
        raise TenantViolation(
            'Bulk UPDATE/DELETE on an OrgScoped model must filter org_id '
            'explicitly (or run under unscoped()).')

    if not state.is_select or state.is_column_load or state.is_relationship_load:
        return

    if org_id is None:
        return

    state.statement = state.statement.options(
        with_loader_criteria(OrgScoped, lambda cls: cls.org_id == org_id,
                             include_aliases=True)
    )


def _persisted_org_id(obj):
    """The org_id this row was loaded with, not the one it now reads.

    Anyone able to put a foreign row in the session can also set its
    org_id to None, and the guard would then wave it through.
    """
    history = sa_inspect(obj).attrs.org_id.history
    return history.deleted[0] if history.deleted else obj.org_id


@event.listens_for(Session, 'before_flush')
def _stamp_org(session, _ctx, _instances):
    org_id = current_org_id()
    if org_id is None and not has_request_context():
        return
    for obj in session.new:
        if isinstance(obj, OrgScoped):
            if obj.org_id is None:
                if org_id is None:
                    log_refusal('tenant_missing_on_create',
                                model=type(obj).__name__)
                    raise TenantViolation(
                        f'{type(obj).__name__} created without a tenant')
                obj.org_id = org_id
            elif org_id is not None and obj.org_id != org_id:
                log_refusal('tenant_write_refused', model=type(obj).__name__,
                            row_org_id=obj.org_id, acting_org_id=org_id)
                raise TenantViolation('Refusing to write across tenants')
    # Updates and deletes too: the read filter makes cross-tenant rows
    # unreachable, but one smuggled in through the identity map must not
    # be written or removed.
    for obj in (*session.dirty, *session.deleted):
        if not isinstance(obj, OrgScoped) or org_id is None:
            continue
        owner = _persisted_org_id(obj)
        if owner is not None and owner != org_id:
            log_refusal('tenant_write_refused', model=type(obj).__name__,
                        row_org_id=owner, acting_org_id=org_id)
            raise TenantViolation('Refusing to write across tenants')


@contextmanager
def unscoped():
    """Disable tenant filtering. Every use should be justified in review."""
    db.session.info['unscoped'] = True
    try:
        yield
    finally:
        db.session.info.pop('unscoped', None)
