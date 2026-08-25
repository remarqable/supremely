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
