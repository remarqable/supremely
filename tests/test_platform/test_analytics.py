"""Analytics provider registry: validation and the layout contract."""

from pathlib import Path

import pytest

from app.platform.analytics import clean_analytics_settings
from app.platform.errors import ValidationError


def test_off_returns_empty(app):
    assert clean_analytics_settings({}) == {}
    assert clean_analytics_settings({'provider': ''}) == {}


def test_plausible_cleaned(app):
    cleaned = clean_analytics_settings({
        'provider': 'plausible',
        'analytics_plausible_script_url':
            '  https://plausible.io/js/pa-abc12345.js  ',
        'analytics_plausible_site_domain': 'acme.example.com',
    })
    assert cleaned == {'provider': 'plausible',
                       'script_url': 'https://plausible.io/js/pa-abc12345.js',
                       'site_domain': 'acme.example.com'}


def test_optional_field_may_be_empty(app):
    cleaned = clean_analytics_settings({
        'provider': 'plausible',
        'analytics_plausible_script_url': 'https://stats.acme.dev/js/script.js',
    })
    assert 'site_domain' not in cleaned


def test_other_providers_fields_are_ignored(app):
    """Every provider's fieldset submits (hidden ones included); only the
    selected provider's fields may reach the stored config."""
    cleaned = clean_analytics_settings({
        'provider': 'fathom',
        'analytics_fathom_site_id': 'ABCDEFGH',
        'analytics_ga4_measurement_id': 'G-ABC1234567',
    })
    assert cleaned == {'provider': 'fathom', 'site_id': 'ABCDEFGH'}


@pytest.mark.parametrize('form', [
    {'provider': 'not-a-tracker'},
    {'provider': 'fathom'},                                     # required missing
    {'provider': 'ga4', 'analytics_ga4_measurement_id': 'UA-123456-7'},
    {'provider': 'plausible',
     'analytics_plausible_script_url': 'http://plausible.io/js/script.js'},
    {'provider': 'plausible',
     'analytics_plausible_script_url': 'javascript:alert(1)'},
    # Attribute/CSP breakout attempts inside an otherwise https URL.
    {'provider': 'plausible',
     'analytics_plausible_script_url': 'https://x.io/a.js" onload="alert(1)'},
    {'provider': 'plausible',
     'analytics_plausible_script_url': 'https://x.io/a.js; script-src *'},
    {'provider': 'umami',
     'analytics_umami_script_url': 'https://u.example/script.js',
     'analytics_umami_website_id': 'not-a-uuid'},
])
def test_malformed_input_rejected(app, form):
    with pytest.raises(ValidationError):
        clean_analytics_settings(form)


def test_every_head_owning_layout_includes_the_analytics_partial(app):
    """The include is the layout contract (docs/themes/building-a-theme.md):
    a theme layout without it silently drops the org's tracker."""
    views = Path(app.root_path) / 'views'
    layouts = [views / 'layouts' / 'community.html',
               *sorted((views / 'themes').glob('*/layout.html'))]
    assert len(layouts) >= 5                # community shell + built-in themes
    for layout in layouts:
        assert "{% include 'partials/_analytics.html' %}" in \
            layout.read_text(encoding='utf-8'), layout
