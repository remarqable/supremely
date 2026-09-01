"""Per-org analytics integrations (Manage → Settings → Analytics).

Each provider is declared once in ANALYTICS_PROVIDERS: the fields an org
admin fills in (with validation), the <head> tags the site emits, and the
hosts the Content-Security-Policy must allow for the tracker's script and
its beacons. Adding a tracker means adding one entry here plus its i18n
keys — the settings form, the layouts, and the CSP hook all read the
registry.

Strict CSP is preserved throughout. The inline bootstrap JS that vendors
ship in their snippets is replaced by tiny first-party files under
app/static/js/analytics/ that read their config from data-* attributes, so
'unsafe-inline' is never needed; third-party script/connect hosts are
allowed only on responses for an org that actually configured that
provider (and never on the /manage or /admin consoles).
"""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from flask import g, url_for
from markupsafe import Markup

from app.platform.errors import ValidationError
from app.platform.i18n import t

GA4_ID_RE = re.compile(r'G-[A-Z0-9]{4,16}')
FATHOM_SITE_RE = re.compile(r'[A-Za-z0-9]{6,16}')
UUID_RE = re.compile(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
HOSTNAME_RE = re.compile(r'[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?')


def _script_origin(config: dict, key: str = 'script_url') -> list[str]:
    parts = urlsplit(config[key])
    return [f'{parts.scheme}://{parts.netloc}']


def _plausible_tags(config: dict) -> list[Markup]:
    domain = config.get('site_domain')
    attr = Markup(' data-domain="{}"').format(domain) if domain else ''
    return [
        Markup('<script async src="{}"{}></script>').format(
            config['script_url'], attr),
        Markup('<script async src="{}"></script>').format(
            url_for('static', filename='js/analytics/plausible-init.js')),
    ]


def _ga4_tags(config: dict) -> list[Markup]:
    return [
        Markup('<script async '
               'src="https://www.googletagmanager.com/gtag/js?id={}">'
               '</script>').format(config['measurement_id']),
        Markup('<script async src="{}" data-measurement-id="{}"></script>')
        .format(url_for('static', filename='js/analytics/ga4-init.js'),
                config['measurement_id']),
    ]


def _fathom_tags(config: dict) -> list[Markup]:
    return [Markup('<script defer src="https://cdn.usefathom.com/script.js" '
                   'data-site="{}"></script>').format(config['site_id'])]


def _umami_tags(config: dict) -> list[Markup]:
    return [Markup('<script defer src="{}" data-website-id="{}"></script>')
            .format(config['script_url'], config['website_id'])]


# Field kinds: 'https_url' (an https:// URL, host allowlisted in CSP),
# 're' (matched in full against 'pattern'). 'placeholder' is shown in the
# form; labels/hints come from i18n keys manage.analytics_<provider>_<field>.
ANALYTICS_PROVIDERS = {
    'plausible': {
        'label': 'Plausible',
        'fields': {
            'script_url': {'kind': 'https_url', 'required': True,
                           'placeholder': 'https://plausible.io/js/pa-XXXXXXXX.js'},
            'site_domain': {'kind': 're', 'pattern': HOSTNAME_RE,
                            'required': False, 'placeholder': 'example.com'},
        },
        'tags': _plausible_tags,
        'csp': lambda config: (_script_origin(config), _script_origin(config)),
    },
    'ga4': {
        'label': 'Google Analytics 4',
        'fields': {
            'measurement_id': {'kind': 're', 'pattern': GA4_ID_RE,
                               'required': True,
                               'placeholder': 'G-XXXXXXXXXX'},
        },
        'tags': _ga4_tags,
        'csp': lambda config: (
            ['https://www.googletagmanager.com'],
            ['https://*.google-analytics.com',
             'https://www.googletagmanager.com',
             'https://*.analytics.google.com'],
        ),
    },
    'fathom': {
        'label': 'Fathom',
        'fields': {
            'site_id': {'kind': 're', 'pattern': FATHOM_SITE_RE,
                        'required': True, 'placeholder': 'ABCDEFGH'},
        },
        'tags': _fathom_tags,
        'csp': lambda config: (['https://cdn.usefathom.com'],
                               ['https://cdn.usefathom.com']),
    },
    'umami': {
        'label': 'Umami',
        'fields': {
            'script_url': {'kind': 'https_url', 'required': True,
                           'placeholder': 'https://cloud.umami.is/script.js'},
            'website_id': {'kind': 're', 'pattern': UUID_RE, 'required': True,
                           'placeholder': '94db1cb1-74f4-4a40-ad6c-962362670409'},
        },
        'tags': _umami_tags,
        'csp': lambda config: (_script_origin(config), _script_origin(config)),
    },
}


def clean_analytics_settings(submitted: Mapping[str, str]) -> dict:
    """Validate the Analytics settings form. Returns the dict stored at
    org.settings['analytics'] ({} when the provider is Off); raises
    ValidationError on anything malformed. Values land in <script> tag
    attributes and in the CSP header, so URLs are https-only and IDs are
    matched strictly — never stored as pasted.
    """
    provider = (submitted.get('provider') or '').strip()
    if not provider:
        return {}
    spec = ANALYTICS_PROVIDERS.get(provider)
    if spec is None:
        raise ValidationError(t('manage.analytics_unknown_provider'))

    cleaned = {'provider': provider}
    for name, field in spec['fields'].items():
        label = t(f'manage.analytics_{provider}_{name}')
        raw = (submitted.get(f'analytics_{provider}_{name}') or '').strip()
        if not raw:
            if field.get('required'):
                raise ValidationError(
                    t('manage.analytics_field_required', field=label))
            continue
        if field['kind'] == 'https_url':
            parts = urlsplit(raw)
            # The host also lands in the CSP header, where quotes, spaces,
            # and semicolons would break out of the source list.
            if (parts.scheme != 'https' or not parts.netloc
                    or any(ch in raw for ch in ' \'";<>')):
                raise ValidationError(
                    t('manage.analytics_field_https', field=label))
        elif not field['pattern'].fullmatch(raw):
            raise ValidationError(
                t('manage.analytics_field_invalid', field=label))
        cleaned[name] = raw
    return cleaned


def _current_config() -> dict:
    org = getattr(g, 'org', None)
    return org.analytics_config() if org else {}


def analytics_head() -> Markup:
    """The configured tracker's <head> tags for the current org, or ''.
    Registered as a Jinja global; every head-owning layout (the community
    shell and each theme's layout.html) includes partials/_analytics.html,
    which renders this.
    """
    config = _current_config()
    spec = ANALYTICS_PROVIDERS.get(config.get('provider'))
    if not spec:
        return Markup('')
    return Markup('\n  ').join(spec['tags'](config))


def analytics_csp_sources() -> tuple[list, list]:
    """(script-src hosts, connect-src hosts) the current org's tracker
    needs, both empty when analytics is off. Consumed by the security-headers
    hook in app/__init__.py."""
    config = _current_config()
    spec = ANALYTICS_PROVIDERS.get(config.get('provider'))
    if not spec:
        return [], []
    return spec['csp'](config)
