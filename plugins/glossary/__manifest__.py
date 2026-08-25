"""Glossary: the reference plugin.

Demonstrates the full plugin surface: install/enable per organization,
settings, a structured Post Type, templates (theme-overridable), Python
behavior (its own model + routes), and disable. No imports, no side effects.
"""

manifest = {
    'slug': 'glossary',
    'name': 'Glossary',
    'description': 'A shared glossary of terms for your organization.',
    'versions': ['1'],
    'default_version': '1',
    'url_prefix': '/glossary',
    'requires': [],
    'nav': [
        {'label': 'glossary.nav', 'path': '', 'permission': 'read',
         'public': True},
    ],
    'settings': {
        'headline': {'type': 'string', 'label': 'Page headline',
                     'default': 'Glossary'},
    },
}
