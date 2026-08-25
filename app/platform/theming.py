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

    app.jinja_env.globals['theme_asset'] = theme_asset
    app.jinja_env.globals['available_themes'] = lambda: AVAILABLE_THEMES


def scan_themes() -> None:
    """Rebuild AVAILABLE_THEMES from both roots."""
    app = current_app
    themes = {
        'default': {'name': 'Supremely Default', 'version': '1.0.0',
                    'author': 'Supremely', 'source': 'builtin', 'path': None,
                    'settings': {}},
    }
    for source, root in (('builtin', builtin_themes_dir(app)),
                         ('installed', installed_themes_dir(app))):
        if not root.exists():
            continue
        for manifest_path in sorted(root.glob('*/theme.json')):
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                slug = manifest.get('slug') or manifest_path.parent.name
                if not THEME_SLUG_RE.fullmatch(slug) or slug == 'default':
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
                }
            except (json.JSONDecodeError, OSError) as e:
                log.error('theme_manifest_invalid', path=str(manifest_path),
                          error=str(e))
    AVAILABLE_THEMES.clear()
    AVAILABLE_THEMES.update(themes)


def current_theme() -> str:
    org = getattr(g, 'org', None)
    theme = org.theme if org else 'default'
    return theme if theme in AVAILABLE_THEMES else 'default'


def theme_config() -> dict:
    """The current org's theme configuration, with schema defaults filled in."""
    org = getattr(g, 'org', None)
    theme = current_theme()
    schema = AVAILABLE_THEMES.get(theme, {}).get('settings', {})
    values = {key: spec.get('default', '') for key, spec in schema.items()}
    if org:
        values.update((org.settings or {}).get('theme_config', {}))
    return values


def render_site(candidates: list[str], **context) -> str:
    """Render through the theme hierarchy: for each candidate in specificity
    order, the active theme's override is tried before the app fallback."""
    from flask import render_template
    theme = current_theme()
    names = []
    for candidate in candidates:
        if theme != 'default':
            names.append(f'themes/{theme}/{candidate}')
        names.append(candidate)
    context.setdefault('theme_settings', theme_config())
    # Site templates extend {{ site_layout }} so a theme's layout override
    # applies even to pages the theme does not override itself. Jinja's
    # extends needs a single name, so resolve the candidate here.
    layout = 'site/layout.html'
    if theme != 'default' and _template_exists(f'themes/{theme}/site/layout.html'):
        layout = f'themes/{theme}/site/layout.html'
    context.setdefault('site_layout', layout)
    return render_template(names, **context)


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
    except zipfile.BadZipFile:
        raise ValidationError('Not a valid ZIP file')

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
        except (json.JSONDecodeError, KeyError):
            raise ValidationError('theme.json is not valid JSON')

        slug = manifest.get('slug', '')
        if not THEME_SLUG_RE.fullmatch(slug) or slug == 'default':
            raise ValidationError('Theme manifest needs a valid slug')

        target = target_root / slug
        for member in names:
            if not member.startswith(prefix) or member.endswith('/'):
                continue
            relative = member[len(prefix):]
            dest = (target / relative).resolve()
            if not dest.is_relative_to(target_root.resolve()):
                raise ValidationError(f'Unsafe path in theme package: {member}')

        for member in names:
            if not member.startswith(prefix) or member.endswith('/'):
                continue
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
