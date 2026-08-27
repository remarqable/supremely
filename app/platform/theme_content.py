"""Theme-declared editable content.

A theme declares an editable content schema in its `theme.json` under
`"content": {"fields": [...]}`. The app renders a schema-driven editor
(Manage → Landing page), stores per-org values, and exposes the resolved
values to templates via the `theme_content()` Jinja global. This is what lets
a marketing theme ship its *design* while each org supplies its own *words*,
with neutral placeholder defaults so activating a theme never clones anyone.

Field types (v1 — deliberately small so it's easy to extend):
    text      one-line string        -> <input type=text>
    textarea  multi-line string      -> <textarea>
    url       a link                 -> <input type=text> (kept simple)
    repeater  a fixed list of groups -> N rows of sub-fields

Each field: {key, type, label, default?, hint?, max?}. A repeater adds
{max_items, item_fields:[...], default:[{...}]} — item_fields are text-like
fields paired by index (e.g. the four feature slots, each keeping its icon).

Extension points for later (Aidan): an `image` type backed by the media
library; per-field validation beyond length; i18n of theme-declared labels;
and richer repeaters (add/remove/reorder). The seams are the type switches in
`_view_field`, `clean`, and `resolve`, plus the editor template's `{% if %}`
ladder — add a branch to each.

Values are rendered into autoescaped HTML, so markup can't break out. That
holds for text inside an element and not for a value placed in an attribute:
a url field needs its scheme checked as well as its length.
"""

from flask import g

TEXT_TYPES = ('text', 'textarea', 'url')
DEFAULT_MAX = 200


def schema(theme: str) -> list:
    """The theme's content field definitions (empty list if it declares none)."""
    from app.platform.theming import AVAILABLE_THEMES
    return (AVAILABLE_THEMES.get(theme, {}).get('content', {}) or {}).get('fields', []) or []


def has_editor(theme: str) -> bool:
    """A theme opts into the Landing editor simply by declaring content fields."""
    return bool(schema(theme))


def _saved_for(theme: str, org) -> dict:
    store = (org.settings or {}).get('theme_content') if org else None
    if not isinstance(store, dict):
        return {}
    value = store.get(theme)
    return value if isinstance(value, dict) else {}


def resolve(theme: str, org=None) -> dict:
    """Resolved values for the theme, defaults filled in for blanks.

    Always returns every declared key, so templates never guard for missing
    fields. Repeaters always return exactly `max_items` rows.
    """
    if org is None:
        org = getattr(g, 'org', None)
    saved = _saved_for(theme, org)
    out = {}
    for field in schema(theme):
        key = field['key']
        if field['type'] == 'repeater':
            out[key] = _resolve_repeater(field, saved.get(key))
        else:
            raw = saved.get(key)
            raw = raw.strip() if isinstance(raw, str) else ''
            out[key] = raw or field.get('default', '')
    return out


def _resolve_repeater(field: dict, saved_items) -> list:
    defaults = field.get('default') or []
    saved_items = saved_items if isinstance(saved_items, list) else []
    rows = []
    for i in range(field.get('max_items', len(defaults))):
        default_item = defaults[i] if i < len(defaults) else {}
        saved_item = saved_items[i] if i < len(saved_items) else {}
        saved_item = saved_item if isinstance(saved_item, dict) else {}
        row = {}
        for sub in field.get('item_fields', []):
            raw = saved_item.get(sub['key'])
            raw = raw.strip() if isinstance(raw, str) else ''
            row[sub['key']] = raw or default_item.get(sub['key'], '')
        rows.append(row)
    return rows


def clean(theme: str, form) -> dict:
    """Parse a submitted editor form into a values dict, applying length caps.

    Field names: a plain field is its `key`; a repeater sub-field is
    `{repeater_key}_{index}_{sub_key}`.
    """
    out = {}
    for field in schema(theme):
        key = field['key']
        if field['type'] == 'repeater':
            rows = []
            for i in range(field.get('max_items', len(field.get('default') or []))):
                row = {}
                for sub in field.get('item_fields', []):
                    raw = form.get(f"{key}_{i}_{sub['key']}", '').strip()
                    row[sub['key']] = raw[:sub.get('max', DEFAULT_MAX)]
                rows.append(row)
            out[key] = rows
        else:
            value = form.get(key, '').strip()[:field.get('max', DEFAULT_MAX)]
            if field['type'] == 'url':
                value = _safe_url(value)
            out[key] = value
    return out


def _safe_url(value: str) -> str:
    """A url field goes straight into an href.

    Autoescaping stops markup breaking out of the attribute but has
    nothing to say about the scheme, so javascript: survives it. Only the
    Content-Security-Policy stops that today, which makes the policy the
    single thing between an editor and every visitor to the public page.
    """
    lowered = value.lower()
    if lowered.startswith('//') or lowered.startswith('/\\'):
        # Protocol-relative, or a backslash a browser reads as a slash:
        # both leave this site while looking like a local path.
        return ''
    return value if lowered.startswith(('http://', 'https://', '/', '#')) else ''


def editor_view(theme: str, org) -> list:
    """A flat view-model for the editor template: one entry per field, with
    inputs already resolved to (name, label, value, placeholder). Keeps the
    template a simple type switch instead of nested index juggling."""
    saved = _saved_for(theme, org)
    view = []
    for field in schema(theme):
        if field['type'] == 'repeater':
            view.append({
                'type': 'repeater',
                'label': field.get('label', field['key']),
                'hint': field.get('hint'),
                'rows': _view_repeater(field, saved.get(field['key'])),
            })
        else:
            view.append(_view_field(field, saved.get(field['key'])))
    return view


def _view_field(field: dict, saved_value) -> dict:
    value = saved_value if isinstance(saved_value, str) else ''
    return {
        'type': field['type'] if field['type'] in TEXT_TYPES else 'text',
        'name': field['key'],
        'label': field.get('label', field['key']),
        'hint': field.get('hint'),
        'value': value,
        'placeholder': field.get('default', ''),
        'max': field.get('max', DEFAULT_MAX),
    }


def _view_repeater(field: dict, saved_items) -> list:
    defaults = field.get('default') or []
    saved_items = saved_items if isinstance(saved_items, list) else []
    rows = []
    for i in range(field.get('max_items', len(defaults))):
        default_item = defaults[i] if i < len(defaults) else {}
        saved_item = saved_items[i] if i < len(saved_items) and isinstance(saved_items[i], dict) else {}
        subs = []
        for sub in field.get('item_fields', []):
            subs.append({
                'type': sub['type'] if sub['type'] in TEXT_TYPES else 'text',
                'name': f"{field['key']}_{i}_{sub['key']}",
                'label': sub.get('label', sub['key']),
                'value': saved_item.get(sub['key'], ''),
                'placeholder': default_item.get(sub['key'], ''),
                'max': sub.get('max', DEFAULT_MAX),
            })
        rows.append({'index': i, 'subs': subs})
    return rows
