"""Themes: organization-selectable presentation packages.

See blueprint/patterns/theming.md. Two theme roots: built-in themes ship in
app/views/themes/; operator-installed ones live on the data volume so they
survive image upgrades. Templates resolve through a candidate list -- never a
per-request loader swap. Installing a theme is deploying code: Platform
Admins only, and tenants never upload template files.
"""

import json
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from flask import (
    current_app,
    g,
    has_app_context,
    has_request_context,
    request,
)
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from app.platform.devices import device_candidates, mobile_variant
from app.platform.errors import ValidationError
from app.platform.logger import get_logger

if TYPE_CHECKING:                       # circular at runtime, fine for hints
    from app.models import Organization

log = get_logger()

# slug -> {'name', 'version', 'author', 'source': 'builtin'|'installed',
#          'path': Path|None, 'settings': {key: {type,label,default}}}
AVAILABLE_THEMES: dict[str, dict] = {}

THEME_SLUG_RE = re.compile(r'[a-z0-9]([a-z0-9-]{1,48}[a-z0-9])?')


# A theme may not declare copy that duplicates something the organization
# already owns. Its name, description and pictures follow it from theme to
# theme; a field asking an admin to retype one of them is a bug, not a
# feature (that is what Manage -> Branding is for).
ORG_OWNED_KEYS = frozenset({
    'brand_name', 'site_name', 'org_name', 'name', 'description',
    'logo', 'favicon', 'hero_image',
})

SETTING_TYPES = ('color', 'number', 'string')


def validate_manifest(manifest: dict) -> None:
    """Raise ValidationError unless this theme.json is usable.

    Called from theme discovery and from the installer, so a manifest that
    parses but is missing a required entry is caught once, at the boundary,
    rather than during someone's page view.
    """
    from app.platform.theme_content import FIELD_TYPES
    if not isinstance(manifest, dict):
        raise ValidationError('theme.json must be a JSON object')
    for key in ('slug', 'name', 'version'):
        if not str(manifest.get(key, '')).strip():
            raise ValidationError(f'theme.json is missing "{key}"')
    if not THEME_SLUG_RE.fullmatch(str(manifest['slug'])):
        raise ValidationError(f'Invalid theme slug: {manifest["slug"]!r}')

    settings = manifest.get('settings') or {}
    if not isinstance(settings, dict):
        raise ValidationError('theme.json "settings" must be an object')
    for key, spec in settings.items():
        if not isinstance(spec, dict):
            raise ValidationError(f'Setting {key} must be an object')
        if spec.get('type', 'string') not in SETTING_TYPES:
            raise ValidationError(
                f'Setting {key} has unknown type {spec.get("type")!r}')

    content = manifest.get('content') or {}
    if not isinstance(content, dict):
        raise ValidationError('theme.json "content" must be an object')
    fields = content.get('fields') or []
    if not isinstance(fields, list):
        raise ValidationError('theme.json "content.fields" must be a list')
    for field in fields:
        if not isinstance(field, dict) or not field.get('key'):
            raise ValidationError('Every content field needs a "key"')
        if field.get('type') not in FIELD_TYPES:
            raise ValidationError(
                f'Content field {field["key"]} has unknown type '
                f'{field.get("type")!r}')
        if field['key'] in ORG_OWNED_KEYS:
            raise ValidationError(
                f'Content field {field["key"]} duplicates something the '
                f'organization owns; read it from g.org instead')


def builtin_themes_dir(app) -> Path:
    return Path(app.root_path) / 'views' / 'themes'


def installed_themes_dir(app) -> Path:
    return Path(app.config['DATA_DIR']) / 'themes'


def init_theming(app) -> None:
    installed = installed_themes_dir(app)
    installed.mkdir(parents=True, exist_ok=True)

    # Installed themes resolve as 'themes/<slug>/...' after the app loader
    # misses (built-in themes win a slug collision on purpose).
    app.jinja_env.loader = ChoiceLoader([
        app.jinja_env.loader,
        PrefixLoader({'themes': FileSystemLoader(str(installed))}),
    ])

    with app.app_context():
        scan_themes()

    from app.platform.analytics import analytics_head
    from app.platform.theme_content import resolve as resolve_content
    app.jinja_env.globals['analytics_head'] = analytics_head
    app.jinja_env.globals['theme_asset'] = theme_asset
    app.jinja_env.globals['themed'] = themed
    app.jinja_env.globals['available_themes'] = lambda: AVAILABLE_THEMES
    app.jinja_env.globals['current_theme'] = current_theme
    # Resolved editable content for the active theme (defaults filled in).
    app.jinja_env.globals['theme_content'] = lambda: resolve_content(current_theme())
    app.jinja_env.globals['community_tokens'] = community_tokens
    app.jinja_env.globals['theme_capabilities'] = theme_capabilities


def scan_themes() -> None:
    """Rebuild AVAILABLE_THEMES from both roots. Origin ships with core and
    is the terminal fallback for every other theme."""
    app = current_app
    themes = {}
    for source, root in (('builtin', builtin_themes_dir(app)),
                         ('installed', installed_themes_dir(app))):
        if not root.exists():
            continue
        for manifest_path in sorted(root.glob('*/theme.json')):
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                manifest.setdefault('slug', manifest_path.parent.name)
                # A built-in theme with a bad manifest is a developer error
                # and fails loudly (CI renders every shipped theme); an
                # operator's third-party theme is skipped with a log line,
                # because it must never take a whole installation down.
                try:
                    validate_manifest(manifest)
                except ValidationError:
                    if source == 'builtin':
                        raise
                    log.error('theme_manifest_invalid',
                              path=str(manifest_path))
                    continue
                slug = manifest['slug']
                if slug in themes:
                    continue            # built-in wins over installed
                themes[slug] = {
                    'name': manifest.get('name', slug),
                    'version': str(manifest.get('version', '0')),
                    'author': manifest.get('author', ''),
                    'source': source,
                    'path': manifest_path.parent,
                    'settings': manifest.get('settings', {}) or {},
                    # Editable content schema (theme_content.py). Declaring any
                    # fields here is what surfaces the Landing editor for the
                    # theme in Manage.
                    'content': manifest.get('content', {}) or {},
                    # What the theme's templates render (see
                    # THEME_CAPABILITY_DEFAULTS). Manage uses this to warn
                    # when an edit won't be visible on the site.
                    'capabilities': manifest.get('capabilities', {}) or {},
                    # Whitelisted brand tokens a theme may tint the app-owned
                    # community shell with (community_tokens()).
                    'community_tokens': manifest.get('community_tokens', {}) or {},
                }
            except (json.JSONDecodeError, OSError) as e:
                log.error('theme_manifest_invalid', path=str(manifest_path),
                          error=str(e))
    if 'origin' not in themes:
        log.error('origin_theme_missing')
    AVAILABLE_THEMES.clear()
    AVAILABLE_THEMES.update(themes)


# The only endpoint whose response may render a theme the org has not
# chosen. Naming it here, rather than trusting whatever set the override,
# keeps a stale value on g from ever reaching a visitor.
PREVIEW_ENDPOINT = 'manage.theme_preview'


# Settings whose values the live preview will take from the URL. Both have
# an exact validator in clean_theme_config. A free-text setting does not,
# and the preview is a GET, so a link could otherwise put a stranger's CSS
# in front of an admin. Free text is still saved and rendered, just not
# previewed before it is saved.
PREVIEWABLE_SETTING_TYPES = ('color', 'number')


def current_theme() -> str:
    # Manage -> Theme renders the home page in a theme the org has not
    # chosen yet, so the picker can be browsed before anything is saved.
    # The override lives here because this is the one place the theme is
    # decided, so everything downstream follows without knowing about it.
    preview = getattr(g, 'preview_theme', None)
    if (preview in AVAILABLE_THEMES and has_request_context()
            and request.endpoint == PREVIEW_ENDPOINT):
        return preview
    return saved_theme(getattr(g, 'org', None))


# Capabilities a theme is assumed to have unless its theme.json says
# otherwise. Permissive defaults: most themes derive from Origin and render
# everything; a minimal theme opts out (e.g. Trailhead's footer shows only
# bottom-bar links, so it declares "footer_groups": false).
THEME_CAPABILITY_DEFAULTS = {'footer_groups': True}


def theme_capabilities(theme: str | None = None) -> dict:
    """The active (or given) theme's declared rendering capabilities, with
    defaults filled in. Themes are still just renderers — this changes no
    behavior, it only lets Manage say "your theme won't show this"."""
    capabilities = dict(THEME_CAPABILITY_DEFAULTS)
    manifest = AVAILABLE_THEMES.get(theme or current_theme(), {})
    capabilities.update(manifest.get('capabilities', {}) or {})
    return capabilities


def saved_theme(org: 'Organization | None') -> str:
    """The theme an organization has chosen, ignoring any preview.

    Also where a stored value that no longer resolves is settled: the legacy
    'default' alias, and a theme that has since been uninstalled. Anything
    reading org.theme raw gets those wrong, which is why current_theme comes
    through here too.
    """
    theme = (org.theme if org else 'origin') or 'origin'
    if theme == 'default':              # legacy alias for the fallback theme
        theme = 'origin'
    return theme if theme in AVAILABLE_THEMES else 'origin'


def theme_setting_values(org: 'Organization | None') -> dict:
    """Each installed theme's settings as the picker should start them.

    The theme in use starts from what was saved; every other theme starts
    from its own defaults, because settings belong to the theme they were
    saved against.
    """
    stored = (org.settings or {}).get('theme_config', {}) if org else {}
    active = saved_theme(org)
    return {slug: {key: (stored.get(key) if slug == active else None)
                   or spec.get('default', '')
                   for key, spec in (info.get('settings') or {}).items()}
            for slug, info in AVAILABLE_THEMES.items()}


def theme_config() -> dict:
    """The current org's theme configuration, with schema defaults filled in."""
    org = getattr(g, 'org', None)
    theme = current_theme()
    schema = AVAILABLE_THEMES.get(theme, {}).get('settings', {})
    values = {key: spec.get('default', '') for key, spec in schema.items()}
    # Stored settings belong to the theme they were saved against. Previewing
    # a different one shows its own defaults rather than borrowing colours
    # from the theme in use, which is also what the picker's form shows.
    if org and theme == saved_theme(org):
        values.update((org.settings or {}).get('theme_config', {}))
    # Manage -> Theme previews settings that have not been saved, so the
    # colour follows the picker. Already validated by the route through
    # clean_theme_config, which is what makes a value safe to interpolate
    # into a <style> block.
    if has_request_context() and request.endpoint == PREVIEW_ENDPOINT:
        values.update(getattr(g, 'preview_config', None) or {})
    return values


HEX_COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}')


def clean_theme_config(theme: str, submitted: dict) -> dict:
    """Validate submitted theme settings against the theme's manifest schema.

    Theme settings are interpolated into a <style> block, where Jinja's HTML
    autoescaping does NOT neutralise CSS metacharacters ({ } ; : ( )). So the
    value must be validated on write, exactly as brand_primary is. Unknown
    keys are dropped; a bad value raises ValidationError.
    """
    schema = AVAILABLE_THEMES.get(theme, {}).get('settings', {})
    cleaned = {}
    for key, spec in schema.items():
        raw = (submitted.get(key) or '').strip()
        if not raw:
            continue
        kind = spec.get('type', 'string')
        label = spec.get('label', key)
        if kind == 'color':
            if not HEX_COLOR_RE.fullmatch(raw):
                raise ValidationError(f'{label} must be a #RRGGBB colour')
        elif kind == 'number':
            if not re.fullmatch(r'-?\d+(\.\d+)?', raw):
                raise ValidationError(f'{label} must be a number')
        else:                           # string: forbid CSS/HTML-breaking chars
            if any(ch in raw for ch in '{}<>;'):
                raise ValidationError(f'{label} contains invalid characters')
        cleaned[key] = raw
    return cleaned


# The community shell is app-owned: themes may tint it through this approved
# whitelist only — never templates or layout. Values are interpolated into a
# <style> block, so anything but #RRGGBB is discarded (CSS injection guard,
# same rationale as Organization.validate).
COMMUNITY_TOKEN_KEYS = ('brand-500', 'brand-600', 'brand-700')


def community_tokens() -> dict:
    """The active theme's approved brand tokens for the community shell."""
    declared = AVAILABLE_THEMES.get(current_theme(), {}).get(
        'community_tokens', {})
    if not isinstance(declared, dict):
        return {}
    return {key: value for key, value in declared.items()
            if key in COMMUNITY_TOKEN_KEYS
            and isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)}


# --- Presentation contexts ----------------------------------------------------
#
# Access decides WHO may see an object; presentation decides HOW it appears
# here. Every render through render_site() declares which surface it is:
#
#   publication  — the org's public-facing site: front page, pages, content
#                  archives and singles, subscribe. Branding belongs to the
#                  theme.
#   application  — the community in use: discussions, members, the pages a
#                  member interacts with. Standardized by Supremely.
#   console      — /manage and /admin. Never themed; renders outside
#                  render_site entirely (listed for vocabulary).
#
# SHELL_CONTEXTS is the single policy point mapping context -> whether the
# standardized community shell renders instead of the theme. The shell now
# serves EVERYONE — visitors included — so a visitor browses the same
# left-nav community members use and sees gated content teased in place
# (tease-don't-hide). Themes style the front page (force_theme) and any
# page rendered with preview/force_theme. Change presentation policy here
# (or per-org later) — never with ad-hoc membership tests at callsites.
# Direction: supremely-dev/docs/"Supremely — Themes, Visibility, and
# Presentation Architecture".

PRESENTATION_CONTEXTS = ('publication', 'application', 'console', 'error')

SHELL_CONTEXTS = {
    'publication': True,     # everyone sees content in-app; the themed
                             # front page remains the public landing
    'application': True,
    # An error is the one page whose surface is unknowable: the URL did not
    # resolve, so there is nothing to ask which surface it belonged to. It
    # renders themed, because the common case by far is a bad or stale link
    # arriving from outside — a typo, an old bookmark, a search result — and
    # such a visitor should land somewhere that still looks like the site
    # they were going to. The cost is that a member who mistypes a community
    # URL sees the public look for one page.
    'error': False,
}


def _use_community_shell(context_name: str, force_theme: bool,
                         context: dict) -> bool:
    """The one place presentation is decided. Previews and force_theme
    always show the public look; everyone else — member or visitor — gets
    the shell where SHELL_CONTEXTS says so."""
    if force_theme or context.get('preview'):
        return False
    return SHELL_CONTEXTS.get(context_name, False)


def render_site(candidates: list[str], context_name: str = 'publication',
                force_theme: bool = False, **context) -> str:
    """Render through the WordPress-style template hierarchy: for each
    candidate in specificity order, try the active theme, then Origin (the
    fallback theme), then core/plugin templates by bare name.

    `context_name` declares the presentation context (see above). When the
    member shell is active, app-owned community templates
    (app/views/community/) win over theme templates: the community surface
    is the application and is never theme-resolvable — themes control the
    public site only."""
    from flask import render_template
    if context_name not in PRESENTATION_CONTEXTS:
        raise ValueError(f'Unknown presentation context: {context_name!r}')
    theme = current_theme()
    shell = _use_community_shell(context_name, force_theme, context)
    # Any community template beats any theme template — the shell is never
    # theme-resolvable. Themes remain the fallback for pages that have no
    # community version yet (e.g. subscribe).
    names = [f'community/{candidate}' for candidate in candidates] if shell else []
    for candidate in candidates:
        if theme != 'origin':
            names.append(f'themes/{theme}/{candidate}')
        names.append(f'themes/origin/{candidate}')
        names.append(candidate)        # core partials and plugin templates
    names = list(dict.fromkeys(names))
    # On a phone, each candidate gains its mobile sibling immediately ahead
    # of it (app/platform/devices.py). A theme or the shell that ships no
    # mobile template is unaffected: the ordinary one still renders.
    names = device_candidates(names)
    context.setdefault('theme_settings', theme_config())
    # Site templates extend {{ site_layout }} so a theme's layout override
    # applies even to pages the theme does not override itself.
    if shell:
        context.setdefault('site_layout', shell_layout())
    else:
        context.setdefault('site_layout', themed('layout.html'))
    return render_template(names, **context)


def shell_layout() -> str:
    """The community shell's layout, mobile version when one exists.

    Exposed to templates as `community_layout` because three shell pages
    extend the layout by name rather than receiving it from render_site;
    without this they would be the only shell surfaces a mobile layout did
    not reach.
    """
    for candidate in device_candidates(['layouts/community.html']):
        if _template_exists(candidate):
            return candidate
    return 'layouts/community.html'


def render_gate(title: str, kind: str | None = None):
    """The members-only gate: a friendly 200 page for an object the visitor
    may know exists but cannot read. Tease-don't-hide is the default stance —
    gated items appear in public lists as locked titles, and clicking one
    lands here: the title, what kind of thing it is, and a login CTA. The
    body never reaches this template; access was already denied by the
    object's own visibility policy (authz.can_view) before rendering.

    Orgs can turn teasing off (Manage → Settings → Privacy). Then this is
    the single point where the gate degrades to hiding: anonymous visitors
    are sent to login (members reach the content after signing in) and
    signed-in non-members get a 404 — the title never renders."""
    from flask import abort, g, redirect, request, url_for
    from flask_login import current_user
    if not g.org.teases_gated_content():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        abort(404)
    return render_site(['gate.html'], gate_title=title, gate_kind=kind,
                       login_next=request.path)


# Surfaces that are not part of an organization's site at all. They render
# outside render_site in normal operation, so their errors do too.
_UNTHEMED_BLUEPRINTS = frozenset({'manage', 'admin', 'setup', 'auth',
                                  'launcher'})
_UNTHEMED_SEGMENTS = frozenset({'manage', 'admin', 'setup', 'auth',
                                'launcher'})


def _outside_the_site(request) -> bool:
    """Is this request on a surface a theme never renders?

    Asks the blueprint first, which is exact. A 404 matched no route and so
    has no blueprint, and only then does the URL decide -- by whole first
    segment, never by string prefix, because a tenant may legitimately
    publish a page at /managers and that page's 404 is the tenant's.
    """
    if request.blueprint in _UNTHEMED_BLUEPRINTS:
        return True
    first = request.path.lstrip('/').split('/', 1)[0]
    return first in _UNTHEMED_SEGMENTS


def render_error(code: int, message: str | None = None) -> str:
    """An error page in the site's own clothes.

    A bad URL on an organization's site used to drop the visitor onto
    Supremely chrome, which reads as a different website. Errors now resolve
    through the same candidate list as every other page, so a theme may ship
    errors/404.html or a single errors/error.html and have it used. Whether
    the theme or the shell renders it is decided by SHELL_CONTEXTS like every
    other surface, not here.

    Falls back to the plain template whenever the themed one cannot be
    rendered: a 500 has already had its transaction rolled back, and an
    error page that raises inside the error handler serves nothing at all.
    """
    from flask import render_template, request
    plain = f'errors/plain/{code}.html'
    if not _template_exists(plain):
        plain = 'errors/plain/error.html'
    if getattr(g, 'org', None) is None or _outside_the_site(request):
        return render_template(plain, code=code, message=message)
    try:
        return render_site([f'errors/{code}.html', 'errors/error.html'],
                           context_name='error', code=code, message=message)
    except Exception:                   # an error page must never fail twice
        log.exception('themed_error_page_failed', code=code)
        return render_template(plain, code=code, message=message)


def themed(name: str) -> str:
    """Resolve a template part through the theme chain: active theme ->
    Origin -> bare name. The Jinja-native get_header()/get_footer():
    layouts do `{% include themed('header.html') %}` so a theme can
    override just one part.

    Device-aware for the same reason render_site is: a theme that wants a
    different header on a phone ships mobile/header.html and gets it, and a
    theme that does not is unaffected."""
    theme = current_theme()
    # Theme first, device second. The other nesting would let Origin's
    # mobile header replace the active theme's own header on a phone, which
    # is the same inversion device_candidates() exists to prevent: a mobile
    # variant may only displace the exact template it is the mobile version
    # of, never one further down the chain.
    roots = [f'themes/{theme}'] if theme != 'origin' else []
    roots.append('themes/origin')
    for root in roots:
        for candidate in device_candidates([name]):
            if _template_exists(f'{root}/{candidate}'):
                return f'{root}/{candidate}'
    return name


def _template_exists(name: str) -> bool:
    """Does this template resolve?

    Memoized per request: the loader reads the whole file to answer, and
    the same names are asked repeatedly: once per part per layout, and
    twice as often on a phone, where each name is tried with its mobile
    sibling. Template files do not appear or vanish mid-request.
    """
    from jinja2 import TemplateNotFound
    seen = g.setdefault('_template_exists', {}) if has_app_context() else {}
    if name in seen:
        return seen[name]
    env = current_app.jinja_env
    try:
        env.loader.get_source(env, name)
        seen[name] = True
    except TemplateNotFound:
        seen[name] = False
    return seen[name]


PAGE_TEMPLATE_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,49}')


def page_template_allowed(name: str | None) -> bool:
    """Is this safe to hand to render_site() as a page template?

    render_site() searches community/ ahead of every theme, then the bare
    name against the whole view tree, so an unchecked value renders an
    application template on a public URL. Two ways that goes wrong: a
    value shaped like a path, and a name the application already owns.

    Enforced at every point of use, not only where the value is written:
    a row stored before this existed is still read on every request.
    """
    if not name or not PAGE_TEMPLATE_RE.fullmatch(name):
        return False
    # Both namespaces: device expansion makes community/mobile/<name>.html a
    # resolvable name too, and one that outranks the theme's.
    return not any(_template_exists(f'community/{candidate}')
                   for candidate in [f'{name}.html',
                                     mobile_variant(f'{name}.html')])


def page_template_exists(name: str) -> bool:
    """Allowed, and provided by the active theme chain.

    The theme half is a write-time courtesy: it gives the author a clear
    error instead of a silent fallback. It is deliberately not enforced
    on read, so switching theme cannot strand a page.
    """
    if not page_template_allowed(name):
        return False
    theme = current_theme()
    return (_template_exists(f'themes/{theme}/{name}.html')
            or _template_exists(f'themes/origin/{name}.html'))


def theme_asset(filename: str) -> str:
    from flask import url_for
    theme = current_theme()
    info = AVAILABLE_THEMES.get(theme, {})
    return url_for('site.theme_static', theme=theme, filename=filename,
                   v=info.get('version', '0'))


# What a theme is made of. Extension-based on purpose: this runs before
# anything is written to disk, where content sniffing cannot help. SVG is
# allowed here and not for tenant uploads, because a theme is code an
# operator deployed and reviewed -- the same trust that lets it ship
# templates at all.
ALLOWED_THEME_SUFFIXES = frozenset({
    '.html', '.json', '.css', '.js', '.map', '.txt', '.md',
    '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico',
    '.woff', '.woff2', '.ttf', '.otf',
})

# Files a repository carries with no extension at all. Without these a
# perfectly ordinary theme ZIP -- one that happens to include its licence --
# would be refused.
ALLOWED_THEME_FILENAMES = frozenset({
    'LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'README', 'AUTHORS',
    'CHANGELOG', 'VERSION',
})


def _packageable(relative: str) -> bool:
    name = Path(relative).name
    return (Path(relative).suffix.lower() in ALLOWED_THEME_SUFFIXES
            or name.upper() in ALLOWED_THEME_FILENAMES)


def install_theme_zip(file) -> str:
    """Unpack a theme ZIP into the installed-themes root. Operator-only --
    a Jinja template is code, so this is a deploy, not tenant self-service."""
    app = current_app
    target_root = installed_themes_dir(app)

    try:
        zf = zipfile.ZipFile(file)
    except zipfile.BadZipFile as exc:
        raise ValidationError('Not a valid ZIP file') from exc

    with zf:
        names = zf.namelist()
        manifest_name = 'theme.json' if 'theme.json' in names else None
        if manifest_name is None:
            # Accept a single top-level directory wrapping the theme.
            roots = {n.split('/', 1)[0] for n in names if '/' in n}
            if len(roots) == 1 and f'{next(iter(roots))}/theme.json' in names:
                prefix = next(iter(roots)) + '/'
            else:
                raise ValidationError('theme.json not found in package')
        else:
            prefix = ''

        try:
            manifest = json.loads(zf.read(prefix + 'theme.json'))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValidationError('theme.json is not valid JSON') from exc

        validate_manifest(manifest)
        slug = manifest['slug']
        if slug in ('default', 'origin'):
            raise ValidationError('That theme slug is reserved')

        target = (target_root / slug).resolve()
        members = [m for m in names
                   if m.startswith(prefix) and not m.endswith('/')]

        # Cap entry count and total uncompressed size (zip-bomb guard).
        MAX_ENTRIES, MAX_TOTAL = 2000, 50 * 1024 * 1024
        if len(members) > MAX_ENTRIES:
            raise ValidationError('Theme package has too many files')
        if sum(zf.getinfo(m).file_size for m in members) > MAX_TOTAL:
            raise ValidationError('Theme package is too large')

        for member in members:
            relative = member[len(prefix):]
            # Confine each member to THIS theme's directory (not just the
            # themes root) so `../other/layout.html` can't overwrite a
            # sibling theme's templates.
            dest = (target / relative).resolve()
            if dest != target and not dest.is_relative_to(target):
                raise ValidationError(f'Unsafe path in theme package: {member}')
            # A theme is templates, styles, scripts, fonts and pictures.
            # Anything else in the archive is something nobody meant to
            # deploy onto the data volume, so the package is refused rather
            # than quietly unpacked minus a file.
            if not _packageable(relative):
                raise ValidationError(
                    f'Theme package contains an unsupported file: {relative}')

        for member in members:
            relative = member[len(prefix):]
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(member))

    scan_themes()
    log.info('theme_installed', slug=slug)
    return slug


def uninstall_theme(slug: str) -> None:
    import shutil

    from app.models import Organization
    info = AVAILABLE_THEMES.get(slug)
    if info is None or info['source'] != 'installed':
        raise ValidationError('Only installed themes can be removed')
    active = Organization.query.filter_by(theme=slug).count()
    if active:
        raise ValidationError(
            f'{active} organization(s) still use this theme. Deactivate first.')
    shutil.rmtree(info['path'], ignore_errors=True)
    scan_themes()
    log.info('theme_uninstalled', slug=slug)
