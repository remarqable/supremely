"""Landing-page copy: the marketing text a themed front page renders.

The Supremely theme's design lives in its templates (layout, structure, the
product mockup); the *words* live here as content, resolved per-org. This is
what keeps a shipped marketing theme from becoming an accidental clone: the
defaults are neutral placeholders, and each org fills in its own copy. Our own
production site is just an org that filled in the template.

Copy is rendered into HTML, where Jinja autoescaping neutralises markup, so no
CSS-style validation (see theming.clean_theme_config) is needed here — only
length caps, applied on write in the controller.
"""

from flask import g

# Four fixed feature slots, paired by index with the icons in front-page.html.
LANDING_DEFAULTS = {
    'headline_lead': 'Your community,',
    'headline_accent': 'all in one place.',
    'subhead': 'Publish updates, send newsletters, and host discussions — '
               'one simple home for your members.',
    'primary_label': 'Get Started',
    'secondary_label': 'View Demo',
    'secondary_url': '/discussions',
    'features': [
        {'title': 'Publish', 'desc': 'Share updates and articles'},
        {'title': 'Newsletter', 'desc': 'Send beautiful emails'},
        {'title': 'Discussions', 'desc': 'Connect and talk with your members'},
        {'title': 'Membership', 'desc': 'Offer access and grow your community'},
    ],
}

_TEXT_KEYS = ('headline_lead', 'headline_accent', 'subhead',
              'primary_label', 'secondary_label', 'secondary_url')

FEATURE_COUNT = len(LANDING_DEFAULTS['features'])


def landing_copy(org=None):
    """The current (or given) org's landing copy, defaults filled in.

    Always returns the full shape: six text fields and exactly FEATURE_COUNT
    features, so templates never guard for missing keys. Unset or blank fields
    fall back to the neutral placeholder, which is why activating the theme on
    a fresh org shows a fillable template, never our copy.
    """
    if org is None:
        org = getattr(g, 'org', None)
    saved = (org.settings or {}).get('landing') if org else None
    saved = saved if isinstance(saved, dict) else {}

    data = {key: saved.get(key) or LANDING_DEFAULTS[key] for key in _TEXT_KEYS}

    saved_features = saved.get('features') or []
    features = []
    for i, default in enumerate(LANDING_DEFAULTS['features']):
        entry = saved_features[i] if i < len(saved_features) else {}
        entry = entry if isinstance(entry, dict) else {}
        features.append({
            'title': (entry.get('title') or '').strip() or default['title'],
            'desc': (entry.get('desc') or '').strip() or default['desc'],
        })
    data['features'] = features
    return data
