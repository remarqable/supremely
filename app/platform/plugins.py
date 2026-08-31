"""Plugin registry, per-tenant version dispatch, and lifecycle.

See blueprint/patterns/plugins.md. Plugin code loads at boot, identically in
every worker; "installed for this tenant" is a row in org_plugin, checked per
request. Each major version mounts privately at /_v/<slug>/<major>/ and a
public dispatcher re-matches the URL against the tenant's pinned version.

Plugins are trusted first-party code. This is not a sandbox.
"""

import importlib
import importlib.util
import pkgutil
from graphlib import TopologicalSorter
from pathlib import Path
from typing import ClassVar

from flask import Blueprint, abort, current_app, g, request, url_for

from app.extensions import db
from app.models.base import transaction
from app.platform.errors import NotFoundError, ValidationError
from app.platform.logger import get_logger

log = get_logger()

REGISTRY: dict[str, dict[str, 'Plugin']] = {}     # slug -> major -> Plugin
MANIFESTS: dict[str, dict] = {}

HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']


class Plugin:
    """Base class for plugin versions. Override what you provide."""

    manifest: ClassVar[dict] = {}

    def blueprint(self):
        """The Flask blueprint for this version, or None."""
        return

    def content_types(self):
        """Content Types this version declares."""
        return []

    def on_install(self, org_id: int) -> None:
        """Seed this tenant's data. Idempotent -- may be retried.
        Writes rows, never DDL: migrations are global."""

    def on_uninstall(self, org_id: int) -> None:
        """Disable-time hook. Do NOT delete tenant data here."""

    def on_upgrade_from(self, org_id: int, previous_major: str) -> None:
        """Move this tenant's data from the previous major's tables."""


def discover() -> list[str]:
    """Slugs of every plugin directory, honouring __DISABLED__ marker files."""
    try:
        import plugins
    except ImportError:
        return []
    root = Path(plugins.__path__[0])
    return sorted(
        module.name for module in pkgutil.iter_modules(plugins.__path__)
        if not module.name.startswith('_')
        and not (root / module.name / '__DISABLED__').exists()
    )


def _in_dependency_order(slugs: list[str]) -> list[str]:
    manifests = {}
    for slug in slugs:
        manifests[slug] = importlib.import_module(
            f'plugins.{slug}.__manifest__').manifest
    graph = {}
    for slug, manifest in manifests.items():
        for dep in manifest.get('requires', []):
            if dep not in manifests:
                raise RuntimeError(f'Plugin {slug} requires missing plugin {dep}')
        graph[slug] = set(manifest.get('requires', []))
    return list(TopologicalSorter(graph).static_order())


def load_plugins(app) -> None:
    """Import every version of every plugin and wire up routing. Boot only."""
    from app.platform.content_types import CONTENT_TYPES, register_content_type
    from app.platform.i18n import merge_translations

    REGISTRY.clear()
    MANIFESTS.clear()

    for slug in _in_dependency_order(discover()):
        manifest = importlib.import_module(f'plugins.{slug}.__manifest__').manifest
        if manifest.get('slug') != slug:
            raise RuntimeError(f'Manifest slug mismatch in plugins/{slug}')
        MANIFESTS[slug] = manifest
        REGISTRY[slug] = {}

        for major in manifest['versions']:
            module = importlib.import_module(f'plugins.{slug}.v{major}')

            # Import models for EVERY version, installed or not: migrations
            # are global and Alembic must see every table.
            # find_spec, not suppress(ModuleNotFoundError): suppressing also
            # swallows a bad import INSIDE models, which silently leaves the
            # plugin's tables off db.metadata and out of every migration.
            if importlib.util.find_spec(f'plugins.{slug}.v{major}.models'):
                importlib.import_module(f'plugins.{slug}.v{major}.models')

            plugin = module.plugin
            bp = plugin.blueprint()
            if bp is not None:
                expected = f'{slug}_v{major}'
                if bp.name != expected:
                    raise RuntimeError(
                        f'Blueprint must be named {expected}, got {bp.name}')
                # Private mount. Never linked, never on the public prefix,
                # and unreachable from outside: see block_private_mounts.
                app.register_blueprint(bp, url_prefix=f'/_v/{slug}/{major}')

            for content_type in plugin.content_types():
                existing = CONTENT_TYPES.get(content_type.slug)
                if existing is None:
                    register_content_type(content_type)
                elif existing.plugin != slug:
                    raise RuntimeError(
                        f'Content type {content_type.slug} already owned by '
                        f'{existing.plugin or "core"}')
                elif existing != content_type:
                    # Two majors of one plugin defining the same slug
                    # differently: the first registered would win silently and
                    # a pinned tenant would get the other one's fields.
                    raise RuntimeError(
                        f'{slug} majors disagree about content type '
                        f'{content_type.slug}')

            lang_dir = Path(module.__path__[0]) / 'lang'
            if lang_dir.exists():
                import json
                for lang_file in lang_dir.glob('*.json'):
                    merge_translations(lang_file.stem,
                                       json.loads(lang_file.read_text('utf-8')))

            REGISTRY[slug][str(major)] = plugin

        if manifest.get('url_prefix'):
            _register_dispatcher(app, slug, manifest)
        log.info('plugin_loaded', slug=slug, versions=manifest['versions'])

    app.jinja_env.globals['plugin_url_for'] = plugin_url_for

    @app.context_processor
    def inject_plugin_nav():
        return {'plugin_nav': visible_nav_entries()}


def block_private_mounts(app) -> None:
    """Make /_v/<slug>/<major>/ unreachable over HTTP.

    Only the public dispatcher gates on installed_version(); the private
    mount answered directly, so any member could read and write a
    plugin's routes for an org that never installed it. The dispatcher
    re-matches the internal URL against the url_map and calls the view
    itself -- it never issues a request whose path is /_v/... -- so a
    request that arrives on that prefix has bypassed the gate by
    definition.
    """
    @app.before_request
    def block_private_mounts():
        if request.path.startswith('/_v/'):
            abort(404)


def _register_dispatcher(app, slug: str, manifest: dict) -> None:
    """One public route per plugin, dispatching to the tenant's pinned
    version by re-matching the URL against the private mounts."""
    public = Blueprint(slug, __name__)

    @public.route('', defaults={'rest': ''}, methods=HTTP_METHODS)
    @public.route('/', defaults={'rest': ''}, methods=HTTP_METHODS)
    @public.route('/<path:rest>', methods=HTTP_METHODS)
    def dispatch(rest):
        g.plugin_slug = slug
        g.plugin_version = installed_version(slug)
        if g.plugin_version is None:
            abort(404)          # not installed for this tenant: does not exist
        if g.plugin_version not in REGISTRY[slug]:
            raise RuntimeError(
                f'org {g.org.id} pinned {slug} v{g.plugin_version}, not on disk')

        adapter = current_app.url_map.bind(request.host)
        endpoint, args = adapter.match(
            f'/_v/{slug}/{g.plugin_version}/{rest}', method=request.method)

        # A direct view call skips blueprint before_request hooks; run them.
        bp_name = endpoint.rsplit('.', 1)[0]
        for fn in current_app.before_request_funcs.get(bp_name, []):
            rv = current_app.ensure_sync(fn)()
            if rv is not None:
                return rv

        return current_app.ensure_sync(
            current_app.view_functions[endpoint])(**args)

    app.register_blueprint(public, url_prefix=manifest['url_prefix'])


def installed_version(slug: str) -> str | None:
    """Major version pinned for the current tenant, or None."""
    from app.models.org_plugin import OrgPlugin
    if 'installed_plugins' not in g:
        org = getattr(g, 'org', None)
        g.installed_plugins = {} if org is None else {
            row.plugin_slug: row.version
            for row in OrgPlugin.query.filter_by(org_id=org.id,
                                                 is_enabled=True)
        }
    return g.installed_plugins.get(slug)


def plugin_url_for(view: str, **values) -> str:
    """Public URL for a view in the current tenant's pinned version.
    url_for inside a version returns the PRIVATE path -- never link it."""
    slug, version = g.plugin_slug, g.plugin_version
    internal = url_for(f'{slug}_v{version}.{view}', **values)
    return internal.replace(f'/_v/{slug}/{version}',
                            MANIFESTS[slug]['url_prefix'], 1)


def plugin_settings(slug: str) -> dict:
    """Current org's settings for a plugin, with schema defaults."""
    from app.models.org_plugin import OrgPlugin
    org = getattr(g, 'org', None)
    schema = MANIFESTS.get(slug, {}).get('settings', {})
    values = {key: spec.get('default', '') for key, spec in schema.items()}
    if org is not None:
        row = OrgPlugin.query.filter_by(org_id=org.id, plugin_slug=slug).first()
        if row is not None:
            values.update(row.settings or {})
    return values


def visible_nav_entries() -> list[dict]:
    from app.platform.authz import can
    items = []
    org = getattr(g, 'org', None)
    if org is None:
        return items
    # Force the memoised per-request lookup so nav reflects this tenant.
    for slug in MANIFESTS:
        version = installed_version(slug)
        if version is None:
            continue
        for entry in REGISTRY[slug][version].manifest.get('nav', []):
            if can(entry.get('permission', 'read')) or entry.get('public'):
                href = MANIFESTS[slug]['url_prefix'] + entry.get('path', '')
                items.append({**entry, 'slug': slug, 'version': version,
                              'href': href})
    return items


# --- Lifecycle -----------------------------------------------------------------

def install(org_id: int, slug: str, version: str | None = None) -> None:
    from app.models.org_plugin import OrgPlugin
    if slug not in REGISTRY:
        raise NotFoundError(f'Unknown plugin: {slug}')
    version = str(version or MANIFESTS[slug]['default_version'])
    if version not in REGISTRY[slug]:
        raise ValidationError(f'{slug} has no major version {version}')
    plugin = REGISTRY[slug][version]

    for dep in plugin.manifest.get('requires', []):
        install(org_id, dep)                   # idempotent: recursion is safe

    with transaction():
        existing = OrgPlugin.query.filter_by(org_id=org_id,
                                             plugin_slug=slug).first()
        if existing:
            existing.is_enabled = True         # re-install keeps the pin
        else:
            db.session.add(OrgPlugin(org_id=org_id, plugin_slug=slug,
                                     version=version))
            plugin.on_install(org_id)          # same transaction
    g.pop('installed_plugins', None)
    log.info('plugin_installed', slug=slug, org_id=org_id, version=version)


def uninstall(org_id: int, slug: str) -> None:
    """Disables; never deletes. Data survives so reinstall restores state."""
    from app.models.org_plugin import OrgPlugin
    installed = _installed_for(org_id)
    dependents = [s for s, m in MANIFESTS.items()
                  if slug in m.get('requires', []) and s in installed]
    if dependents:
        raise ValidationError(f'Uninstall {", ".join(dependents)} first')

    row = OrgPlugin.query.filter_by(org_id=org_id, plugin_slug=slug).first()
    if row is None:
        return
    with transaction():
        row.is_enabled = False
        plugin = REGISTRY.get(slug, {}).get(row.version)
        if plugin is not None:
            plugin.on_uninstall(org_id)
    g.pop('installed_plugins', None)
    log.info('plugin_uninstalled', slug=slug, org_id=org_id)


def upgrade(org_id: int, slug: str, to_major: str) -> None:
    """Explicit per-tenant upgrade. Old tables are NOT cleared: rollback is a
    version-column flip, not a restore from backup."""
    from app.models.base import utcnow
    from app.models.org_plugin import OrgPlugin
    row = OrgPlugin.query.filter_by(org_id=org_id, plugin_slug=slug).first()
    if row is None:
        raise NotFoundError(f'{slug} is not installed')
    to_major = str(to_major)
    if row.version == to_major:
        return
    if to_major not in REGISTRY.get(slug, {}):
        raise ValidationError(f'{slug} has no major version {to_major}')

    plugin = REGISTRY[slug][to_major]
    with transaction():
        plugin.on_upgrade_from(org_id, row.version)
        row.version = to_major
        row.upgraded_at = utcnow()
    g.pop('installed_plugins', None)
    log.info('plugin_upgraded', slug=slug, org_id=org_id, to=to_major)


def _installed_for(org_id: int) -> set[str]:
    import sqlalchemy as sa

    from app.models.org_plugin import OrgPlugin
    return set(db.session.scalars(
        sa.select(OrgPlugin.plugin_slug)
        .where(OrgPlugin.org_id == org_id, OrgPlugin.is_enabled.is_(True))))


def check_stranded_pins(app) -> None:
    """Refuse to serve tenants pinned to versions no longer on disk. Turns
    'we deleted v1 while a customer was on it' into a failed boot."""
    import sqlalchemy as sa

    from app.models.org_plugin import OrgPlugin
    try:
        with app.app_context():
            pins = db.session.execute(
                sa.select(OrgPlugin.plugin_slug, OrgPlugin.version)
                .where(OrgPlugin.is_enabled.is_(True)).distinct()).all()
    except Exception:       # noqa: BLE001 -- table absent on first boot/pre-migrate
        return
    for slug, version in pins:
        if version not in REGISTRY.get(slug, {}):
            raise RuntimeError(
                f'Tenants are pinned to {slug} v{version}, which is not on disk')
