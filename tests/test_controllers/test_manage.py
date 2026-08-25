import io

from flask import g

from app.extensions import db
from app.models import Content, Membership, NavigationItem, Organization, Upload
from tests.conftest import login_as, make_png, make_user

ACME = 'http://acme.example.test'


def test_manage_requires_permission(app, client, acme, globex):
    member = make_user(email='plain@example.com')
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/manage/content/page', base_url=ACME).status_code == 403


def test_manage_requires_login(client, acme, globex):
    response = client.get('/manage/content/page', base_url=ACME)
    assert response.status_code == 302


def test_create_and_publish_page(app, client, acme, globex, user):
    login_as(client, user)      # owner of acme
    response = client.post('/manage/content/page/new', base_url=ACME, data={
        'title': 'Features', 'slug': 'features', 'body': '## Great stuff',
        'visibility': 'public', 'action': 'publish',
    })
    assert response.status_code == 302
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content.published_by_slug('page', 'features')
        assert page is not None
        assert page.created_by_id == user.id       # audit trail

    public = client.get('/features', base_url=ACME)
    assert b'Great stuff' in public.data


def test_create_and_publish_article(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/content/article/new', base_url=ACME, data={
        'title': 'News', 'slug': 'news', 'body': 'Big **news**.',
        'visibility': 'public', 'action': 'publish'})
    public = client.get('/blog/news', base_url=ACME)
    assert public.status_code == 200
    assert b'Big' in public.data


def test_edit_and_unpublish(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/content/page/new', base_url=ACME, data={
        'title': 'P', 'slug': 'p', 'body': 'x', 'visibility': 'public',
        'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page_id = Content.published_by_slug('page', 'p').id

    client.post(f'/manage/content/{page_id}/edit', base_url=ACME, data={
        'title': 'P2', 'slug': 'p', 'body': 'y', 'visibility': 'public',
        'action': 'unpublish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = db.session.get(Content, page_id)
        assert page.title == 'P2'
        assert not page.is_published


def test_cannot_edit_other_orgs_page(app, client, acme, globex, user):
    with app.test_request_context():
        g.org = globex
        other = Content(type='page', title='G', slug='g', org_id=globex.id,
                        fields={}, tags=[])
        other.save()
        other_id = other.id
        db.session.expunge(other)   # requests never share an identity map
    login_as(client, user)
    response = client.post(f'/manage/content/{other_id}/edit', base_url=ACME,
                           data={'title': 'HACKED', 'slug': 'g', 'body': ''})
    assert response.status_code == 404      # tenant filter: row unreachable


def test_navigation_crud(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'primary', 'label': 'Docs', 'url': 'https://docs.test'})
    client.post('/manage/navigation', base_url=ACME, data={
        'menu': 'primary', 'label': 'Blog', 'url': '/blog-page'})

    def labels():
        with app.test_request_context(base_url=ACME):
            g.org = acme
            return [i.label for i in NavigationItem.items_for('primary')]

    # Appended after the seeded starter navigation, in order.
    assert labels()[-2:] == ['Docs', 'Blog']
    with app.test_request_context(base_url=ACME):
        g.org = acme
        blog_id = next(i.id for i in NavigationItem.items_for('primary')
                       if i.label == 'Blog')

    client.post(f'/manage/navigation/{blog_id}/move', base_url=ACME,
                data={'direction': 'up'})
    assert labels()[-2:] == ['Blog', 'Docs']

    client.post(f'/manage/navigation/{blog_id}/delete', base_url=ACME)
    assert 'Blog' not in labels()
    assert labels()[-1] == 'Docs'


def test_media_upload_and_serve(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'logo.png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 302

    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload = Upload.query.first()
        assert upload.content_type == 'image/png'
        assert upload.has_variants
        upload_id = upload.id

    served = client.get(f'/files/{upload_id}/thumb', base_url=ACME)
    assert served.status_code == 200
    assert served.mimetype == 'image/webp'

    original = client.get(f'/files/{upload_id}/original', base_url=ACME)
    assert original.mimetype == 'image/png'


def test_upload_rejects_disguised_type(client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(b'<script>evil()</script>'), 'not-really.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'not allowed' in response.data


def test_upload_isolated_across_tenants(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/media', base_url=ACME, data={
        'file': (io.BytesIO(make_png()), 'private-to-acme.png'),
    }, content_type='multipart/form-data')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        upload_id = Upload.query.first().id

    # Fetch through globex's host: the row is unreachable there.
    response = client.get(f'/files/{upload_id}/original',
                          base_url='http://globex.example.test')
    assert response.status_code == 404

    # And on the bare installation host (g.org is None, two orgs exist) the
    # file route resolves no tenant and must not serve any org's upload.
    bare = client.get(f'/files/{upload_id}/original', base_url='http://example.test')
    assert bare.status_code == 404


def test_branding_settings(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/settings', base_url=ACME, data={
        'section': 'branding', 'name': 'Acme Community',
        'description': 'We make things.', 'brand_primary': '#ff5500',
        'logo_upload_id': '', 'favicon_upload_id': '',
    })
    org = db.session.get(Organization, acme.id)
    assert org.brand_primary == '#ff5500'

    home = client.get('/', base_url=ACME)
    assert b'#ff5500' in home.data          # brand variable in the page


def test_invalid_brand_color_rejected(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/settings', base_url=ACME, data={
        'section': 'branding', 'name': 'Acme',
        'brand_primary': 'red; } body { display:none',
    }, follow_redirects=True)
    assert b'RRGGBB' in response.data
    assert db.session.get(Organization, acme.id).brand_primary is None


def test_theme_switch(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/settings', base_url=ACME, data={
        'section': 'theme', 'theme': 'midnight', 'theme_accent': '#22ccff',
    })
    org = db.session.get(Organization, acme.id)
    assert org.theme == 'midnight'
    assert org.setting('theme_config')['accent'] == '#22ccff'

    home = client.get('/', base_url=ACME)
    assert b'#22ccff' in home.data          # theme setting reaches the page


def test_landing_editor_saves_copy(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/landing', base_url=ACME, data={
        'headline_lead': 'The open-source',
        'headline_accent': 'community platform.',
        'subhead': 'A simple home for your members.',
        'primary_label': 'Get Started', 'secondary_label': 'View Demo',
        'secondary_url': '/discussions',
        'feature_0_title': 'Publish', 'feature_0_desc': 'Share articles',
    })
    assert response.status_code == 302
    saved = db.session.get(Organization, acme.id).setting('landing')
    assert saved['headline_lead'] == 'The open-source'
    assert saved['features'][0] == {'title': 'Publish', 'desc': 'Share articles'}
    assert len(saved['features']) == 4      # always the four fixed slots


def test_landing_nav_only_for_marketing_theme(app, client, acme, globex, user):
    login_as(client, user)
    # Origin has no landing hero -> no editor entry.
    acme.theme = 'origin'; acme.save()
    assert b'/manage/landing' not in client.get('/manage/settings', base_url=ACME).data
    # The marketing theme surfaces it.
    acme.theme = 'supremely'; acme.save()
    assert b'/manage/landing' in client.get('/manage/settings', base_url=ACME).data
