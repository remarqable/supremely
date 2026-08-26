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

from flask import current_app, g
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from app.platform.errors import ValidationError
from app.platform.logger import get_logger

log = get_logger()

# slug -> {'name', 'version', 'author', 'source': 'builtin'|'installed',
#          'path': Path|None, 'settings': {key: {type,label,default}}}
AVAILABLE_THEMES: dict[str, dict] = {}

THEME_SLUG_RE = re.compile(r'[a-z0-9]([a-z0-9-]{1,48}[a-z0-9])?')


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

    from app.platform.theme_content import resolve as resolve_content
    app.jinja_env.globals['theme_asset'] = theme_asset
    app.jinja_env.globals['themed'] = themed
    app.jinja_env.globals['available_themes'] = lambda: AVAILABLE_THEMES
    app.jinja_env.globals['current_theme'] = current_theme
    # Resolved editable content for the active theme (defaults filled in).
    app.jinja_env.globals['theme_content'] = lambda: resolve_content(current_theme())


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
                slug = manifest.get('slug') or manifest_path.parent.name
                if not THEME_SLUG_RE.fullmatch(slug):
                    continue
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
                }
            except (json.JSONDecodeError, OSError) as e:
                log.error('theme_manifest_invalid', path=str(manifest_path),
                          error=str(e))
    if 'origin' not in themes:
        log.error('origin_theme_missing')
    AVAILABLE_THEMES.clear()
    AVAILABLE_THEMES.update(themes)


def current_theme() -> str:
    org = getattr(g, 'org', None)
    theme = org.theme if org else 'origin'
    if theme == 'default':              # legacy alias for the fallback theme
        theme = 'origin'
    return theme if theme in AVAILABLE_THEMES else 'origin'


def theme_config() -> dict:
    """The current org's theme configuration, with schema defaults filled in."""
    org = getattr(g, 'org', None)
    theme = current_theme()
    schema = AVAILABLE_THEMES.get(theme, {}).get('settings', {})
    values = {key: spec.get('default', '') for key, spec in schema.items()}
    if org:
        values.update((org.settings or {}).get('theme_config', {}))
    return values


_COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}')


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
            if not _COLOR_RE.fullmatch(raw):
                raise ValidationError(f'{label} must be a #RRGGBB colour')
        elif kind == 'number':
            if not re.fullmatch(r'-?\d+(\.\d+)?', raw):
                raise ValidationError(f'{label} must be a number')
        else:                           # string: forbid CSS/HTML-breaking chars
            if any(ch in raw for ch in '{}<>;'):
                raise ValidationError(f'{label} contains invalid characters')
        cleaned[key] = raw
    return cleaned


def render_site(candidates: list[str], **context) -> str:
    """Render through the WordPress-style template hierarchy: for each
    candidate in specificity order, try the active theme, then Origin (the
    fallback theme), then core/plugin templates by bare name."""
    from flask import render_template
    theme = current_theme()
    names = []
    for candidate in candidates:
        if theme != 'origin':
            names.append(f'themes/{theme}/{candidate}')
        names.append(f'themes/origin/{candidate}')
        names.append(candidate)        # core partials and plugin templates
    names = list(dict.fromkeys(names))
    context.setdefault('theme_settings', theme_config())
    # Site templates extend {{ site_layout }} so a theme's layout override
    # applies even to pages the theme does not override itself.
    context.setdefault('site_layout', themed('layout.html'))
    return render_template(names, **context)


def themed(name: str) -> str:
    """Resolve a template part through the theme chain: active theme ->
    Origin -> bare name. The Jinja-native get_header()/get_footer():
    layouts do `{% include themed('header.html') %}` so a theme can
    override just one part."""
    theme = current_theme()
    if theme != 'origin' and _template_exists(f'themes/{theme}/{name}'):
        return f'themes/{theme}/{name}'
    if _template_exists(f'themes/origin/{name}'):
        return f'themes/origin/{name}'
    return name


def _template_exists(name: str) -> bool:
    from jinja2 import TemplateNotFound
    env = current_app.jinja_env
    try:
        env.loader.get_source(env, name)
        return True
    except TemplateNotFound:
        return False


def theme_asset(filename: str) -> str:
    from flask import url_for
    theme = current_theme()
    info = AVAILABLE_THEMES.get(theme, {})
    return url_for('site.theme_static', theme=theme, filename=filename,
                   v=info.get('version', '0'))


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

        slug = manifest.get('slug', '')
        if not THEME_SLUG_RE.fullmatch(slug) or slug in ('default', 'origin'):
            raise ValidationError('Theme manifest needs a valid slug')

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
