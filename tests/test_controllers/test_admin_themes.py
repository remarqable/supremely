import io
import json
import zipfile

from app.platform.theming import AVAILABLE_THEMES


def make_theme_zip(slug='sunrise', extra_files=None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('theme.json', json.dumps({
            'slug': slug, 'name': slug.title(), 'version': '1.0.0',
        }))
        zf.writestr('layout.html',
                    '<!DOCTYPE html><html><body class="sunrise">'
                    '{% block content %}{% endblock %}</body></html>')
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def test_builtin_themes_scanned(app):
    assert 'origin' in AVAILABLE_THEMES
    assert AVAILABLE_THEMES['origin']['source'] == 'builtin'
    assert 'midnight' in AVAILABLE_THEMES
    assert AVAILABLE_THEMES['midnight']['source'] == 'builtin'
    assert 'supremely' in AVAILABLE_THEMES
    assert AVAILABLE_THEMES['supremely']['source'] == 'builtin'


def test_supremely_marketing_theme_renders(client, app, acme, globex):
    """The Supremely marketing theme paints the bespoke landing page —
    gradient headline, feature strip, and the product mockup — on the
    bare-domain home, falling back to Origin for parts it doesn't override."""
    acme.theme = 'supremely'
    acme.save()
    response = client.get('/', base_url='http://acme.example.test')
    assert response.status_code == 200
    body = response.data
    assert b'sup-gradient' in body                      # gradient headline
    assert b'themes/supremely/static/theme.css' in body  # ships its own CSS
    assert b'Welcome to our new home' in body           # product mockup
    assert b'Upcoming Event' in body


def test_supremely_default_copy_is_neutral_not_a_clone(client, app, acme, globex):
    """A fresh org on the theme shows placeholder copy — never Supremely's own
    marketing words. This is what stops accidental clones."""
    acme.theme = 'supremely'
    acme.save()
    body = client.get('/', base_url='http://acme.example.test').data
    assert b'Your community,' in body                    # neutral placeholder
    assert b'open-source' not in body                    # our copy stays ours
    assert b'community platform' not in body


def test_landing_copy_is_editable_per_org(client, app, acme, globex):
    """Each org supplies its own copy; the design stays put."""
    acme.theme = 'supremely'
    acme.update_settings(landing={
        'headline_lead': 'The open-source',
        'headline_accent': 'community platform.',
        'subhead': 'A simple home for your members.',
        'features': [],
    })
    acme.save()
    body = client.get('/', base_url='http://acme.example.test').data
    assert b'The open-source' in body
    assert b'community platform.' in body
    assert b'Publish' in body                            # blank feature -> default
    assert b'sup-gradient' in body                       # design unchanged


def test_install_theme(admin_client, app):
    response = admin_client.post('/admin/themes/install', data={
        'package': (make_theme_zip(), 'sunrise.zip'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'sunrise' in response.data
    assert AVAILABLE_THEMES['sunrise']['source'] == 'installed'


def test_install_rejects_traversal(admin_client, app):
    evil = make_theme_zip(extra_files={'../../escape.txt': 'boom'})
    response = admin_client.post('/admin/themes/install', data={
        'package': (evil, 'evil.zip'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'Unsafe path' in response.data
    assert 'evil' not in AVAILABLE_THEMES


def test_install_rejects_garbage(admin_client, app):
    response = admin_client.post('/admin/themes/install', data={
        'package': (io.BytesIO(b'not a zip'), 'x.zip'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'valid ZIP' in response.data


def test_uninstall_refused_while_in_use(admin_client, app, acme):
    admin_client.post('/admin/themes/install', data={
        'package': (make_theme_zip('inuse'), 'inuse.zip'),
    }, content_type='multipart/form-data')
    acme.theme = 'inuse'
    acme.save()
    response = admin_client.post('/admin/themes/inuse/uninstall',
                                 follow_redirects=True)
    assert b'still use this theme' in response.data
    assert 'inuse' in AVAILABLE_THEMES

    acme.theme = 'origin'
    acme.save()
    admin_client.post('/admin/themes/inuse/uninstall')
    assert 'inuse' not in AVAILABLE_THEMES


def test_installed_theme_renders(admin_client, client, app, acme, globex):
    admin_client.post('/admin/themes/install', data={
        'package': (make_theme_zip('sunrise'), 'sunrise.zip'),
    }, content_type='multipart/form-data')
    acme.theme = 'sunrise'
    acme.save()
    response = client.get('/', base_url='http://acme.example.test')
    assert b'class="sunrise"' in response.data
