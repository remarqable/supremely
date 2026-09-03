import io
import re

from flask import g

from app.extensions import db
from app.models import (
    Content,
    Membership,
    NavigationItem,
    Organization,
    Upload,
    User,
)
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
    client.post('/manage/branding', base_url=ACME, data={
        'name': 'Acme Community',
        'description': 'We make things.', 'brand_primary': '#ff5500',
        'logo_upload_id': '', 'favicon_upload_id': '',
    })
    org = db.session.get(Organization, acme.id)
    assert org.brand_primary == '#ff5500'

    home = client.get('/', base_url=ACME)
    assert b'#ff5500' in home.data          # brand variable in the page


def test_saving_content_without_a_template_does_not_warn(app, client, acme,
                                                          globex, user):
    """The guard is for a stored template the rule now refuses. Content with no
    template at all was tripping it, so every save warned about "None"."""
    login_as(client, user)
    response = client.post('/manage/content/page/new', base_url=ACME, data={
        'title': 'Plain page', 'slug': 'plain-page', 'body': 'Hello',
        'status': 'published', 'visibility': 'public', 'template': '',
    }, follow_redirects=True)
    assert b'no longer allowed' not in response.data
    assert b'&#34;None&#34;' not in response.data


def test_saving_content_still_clears_a_disallowed_template(app, client, acme,
                                                          globex, user):
    """The case the guard exists for: a value stored before the rule existed."""
    login_as(client, user)
    client.post('/manage/content/page/new', base_url=ACME, data={
        'title': 'Legacy', 'slug': 'legacy', 'body': 'x',
        'status': 'published', 'visibility': 'public',
    })
    page = Content.query.filter_by(slug='legacy').first()
    page.template = 'single'            # an application template: refused
    db.session.commit()
    content_id = page.id

    response = client.post(f'/manage/content/{content_id}/edit', base_url=ACME,
                           data={'title': 'Legacy', 'slug': 'legacy',
                                 'body': 'x', 'status': 'published',
                                 'visibility': 'public'},
                           follow_redirects=True)
    assert b'no longer allowed' in response.data
    assert db.session.get(Content, content_id).template is None


def test_settings_ships_no_inline_event_handlers(app, client, acme, globex, user):
    """The CSP has no unsafe-inline, so an on*= attribute is dead markup. This
    is checkable server-side even though the enforcement is the browser's."""
    login_as(client, user)
    for path in ('/manage/branding', '/manage/theme', '/manage/analytics',
                 '/manage/settings/privacy'):
        html = client.get(path, base_url=ACME).get_data(as_text=True)
        assert re.search(r'\son[a-z]+\s*=', html) is None, \
            f'inline handler on {path}'
    html = client.get('/manage/branding', base_url=ACME).get_data(as_text=True)
    assert 'x-model="hex"' in html          # the picker is bound through Alpine


def test_invalid_brand_color_rejected(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/branding', base_url=ACME, data={
        'name': 'Acme',
        'brand_primary': 'red; } body { display:none',
    }, follow_redirects=True)
    assert b'RRGGBB' in response.data
    assert db.session.get(Organization, acme.id).brand_primary is None


PLAUSIBLE_URL = 'https://plausible.io/js/pa-abc12345.js'
BASELINE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


def save_analytics(client, **data):
    return client.post('/manage/analytics', base_url=ACME, data=data)


def test_analytics_saved_and_rendered_on_public_site(app, client, acme,
                                                     globex, user):
    login_as(client, user)
    save_analytics(client, provider='plausible',
                   analytics_plausible_script_url=PLAUSIBLE_URL)
    org = db.session.get(Organization, acme.id)
    assert org.analytics_config() == {'provider': 'plausible',
                                      'script_url': PLAUSIBLE_URL}

    home = client.get('/', base_url=ACME)       # the themed front page
    assert PLAUSIBLE_URL.encode() in home.data
    assert b'/static/js/analytics/plausible-init.js' in home.data
    csp = home.headers['Content-Security-Policy']
    assert "script-src 'self' 'unsafe-eval' https://plausible.io" in csp
    assert "connect-src 'self' https://plausible.io" in csp


def test_analytics_rendered_on_the_community_shell(app, client, acme,
                                                   globex, user):
    login_as(client, user)
    save_analytics(client, provider='plausible',
                   analytics_plausible_script_url=PLAUSIBLE_URL)
    dashboard = client.get('/dashboard', base_url=ACME)
    assert b'/static/js/analytics/plausible-init.js' in dashboard.data


def test_analytics_ga4_tags_and_csp(app, client, acme, globex, user):
    login_as(client, user)
    save_analytics(client, provider='ga4',
                   analytics_ga4_measurement_id='G-ABC1234567')
    home = client.get('/', base_url=ACME)
    assert b'googletagmanager.com/gtag/js?id=G-ABC1234567' in home.data
    assert b'data-measurement-id="G-ABC1234567"' in home.data
    assert b'/static/js/analytics/ga4-init.js' in home.data
    csp = home.headers['Content-Security-Policy']
    assert 'https://www.googletagmanager.com' in csp
    assert "connect-src 'self' https://*.google-analytics.com" in csp


def test_analytics_absent_by_default(client, acme, globex):
    home = client.get('/', base_url=ACME)
    assert b'/static/js/analytics/' not in home.data
    assert home.headers['Content-Security-Policy'] == BASELINE_CSP


def test_analytics_is_tenant_scoped(app, client, acme, globex, user):
    login_as(client, user)
    save_analytics(client, provider='plausible',
                   analytics_plausible_script_url=PLAUSIBLE_URL)
    other = client.get('/', base_url='http://globex.example.test')
    assert b'plausible' not in other.data
    assert other.headers['Content-Security-Policy'] == BASELINE_CSP


def test_analytics_never_on_the_console(app, client, acme, globex, user):
    login_as(client, user)
    save_analytics(client, provider='plausible',
                   analytics_plausible_script_url=PLAUSIBLE_URL)
    console = client.get('/manage/analytics', base_url=ACME)
    # The saved URL appears in the form field, but the tracker itself
    # must not load and the CSP must stay at the strict baseline.
    assert b'/static/js/analytics/plausible-init.js' not in console.data
    assert console.headers['Content-Security-Policy'] == BASELINE_CSP


def test_analytics_invalid_value_flashes_and_persists_nothing(app, client,
                                                              acme, globex,
                                                              user):
    login_as(client, user)
    response = save_analytics(client, provider='ga4',
                              analytics_ga4_measurement_id='UA-123456-7')
    assert response.status_code == 302
    followed = client.get('/manage/analytics', base_url=ACME)
    assert b'Measurement ID' in followed.data       # the flashed error
    assert db.session.get(Organization, acme.id).analytics_config() == {}


def test_analytics_off_clears_config(app, client, acme, globex, user):
    login_as(client, user)
    save_analytics(client, provider='plausible',
                   analytics_plausible_script_url=PLAUSIBLE_URL)
    save_analytics(client, provider='')
    assert db.session.get(Organization, acme.id).analytics_config() == {}
    home = client.get('/', base_url=ACME)
    assert b'/static/js/analytics/' not in home.data
    assert home.headers['Content-Security-Policy'] == BASELINE_CSP


def test_settings_url_redirects_to_its_first_subpage(app, client, acme,
                                                     globex, user):
    login_as(client, user)
    response = client.get('/manage/settings', base_url=ACME)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/manage/settings/privacy')


def test_subnav_column_renders_only_under_settings(app, client, acme, globex,
                                                   user):
    login_as(client, user)
    branding = client.get('/manage/branding', base_url=ACME).data
    # The left nav: Appearance pages plus the Settings parent (which links to
    # its first sub-page). Settings sub-pages appear only in the sub-nav.
    for link in (b'/manage/branding', b'/manage/theme', b'/manage/navigation',
                 b'/manage/plugins', b'/manage/settings/privacy'):
        assert link in branding
    assert b'/manage/analytics' not in branding
    assert b'md:w-44' not in branding       # no second column on a plain page

    privacy = client.get('/manage/settings/privacy', base_url=ACME).data
    assert b'md:w-44' in privacy            # the Settings sub-nav column
    for link in (b'/manage/analytics', b'/manage/domains'):
        assert link in privacy              # its siblings in the sub-nav

    # A sub-page reached directly still shows the column and its siblings.
    analytics = client.get('/manage/analytics', base_url=ACME).data
    assert b'md:w-44' in analytics
    assert b'/manage/domains' in analytics


def test_theme_switch(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/theme', base_url=ACME, data={
        'theme': 'midnight', 'theme_accent': '#22ccff',
    })
    org = db.session.get(Organization, acme.id)
    assert org.theme == 'midnight'
    assert org.setting('theme_config')['accent'] == '#22ccff'

    home = client.get('/', base_url=ACME)
    assert b'#22ccff' in home.data          # theme setting reaches the page


def test_landing_editor_saves_copy(app, client, acme, globex, user):
    acme.theme = 'supremely'        # editor uses the active theme's schema
    acme.save()
    login_as(client, user)
    response = client.post('/manage/landing', base_url=ACME, data={
        'headline_lead': 'The open-source',
        'headline_accent': 'community platform.',
        'subhead': 'A simple home for your members.',
        'primary_label': 'Get Started', 'secondary_label': 'View Demo',
        'secondary_url': '/discussions',
        'features_0_title': 'Publish', 'features_0_desc': 'Share articles',
    })
    assert response.status_code == 302
    saved = db.session.get(Organization, acme.id).setting('theme_content')['supremely']
    assert saved['headline_lead'] == 'The open-source'
    assert saved['features'][0] == {'title': 'Publish', 'desc': 'Share articles'}
    assert len(saved['features']) == 4          # always the four fixed slots


def test_theme_editor_is_always_in_the_nav(app, client, acme, globex, user):
    """The entry does not come and go with the active theme: a theme that
    declares no editable copy still has the page, which explains itself."""
    login_as(client, user)
    for theme in ('origin', 'supremely', 'midnight'):
        acme.theme = theme
        acme.save()
        assert b'/manage/landing' in client.get('/manage/branding',
                                                base_url=ACME).data
        assert client.get('/manage/landing', base_url=ACME).status_code == 200


def test_theme_editor_explains_itself_when_nothing_is_editable(app, client, acme,
                                                               globex, user):
    login_as(client, user)
    acme.theme = 'midnight'                 # declares no content fields
    acme.save()
    body = client.get('/manage/landing', base_url=ACME).data
    assert b'no editable text of its own' in body
    assert b'name="headline"' not in body   # no empty form to fill in


# --- media visibility ----------------------------------------------------------------

def _upload(client, visibility=None, base_url=ACME):
    data = {'file': (io.BytesIO(make_png()), 'photo.png')}
    if visibility is not None:
        data['visibility'] = visibility
    client.post('/manage/media', base_url=base_url, data=data,
                content_type='multipart/form-data')
    return Upload.query.order_by(Upload.id.desc()).first().id


def test_an_upload_can_be_kept_for_members(app, client, acme, globex, user):
    """Every upload used to be public with no way to change it, so a file
    attached to members-only content was served to anyone who guessed its id."""
    login_as(client, user)
    upload_id = _upload(client, 'members')

    assert app.test_client().get(f'/files/{upload_id}/original',
                                 base_url=ACME).status_code == 404
    assert client.get(f'/files/{upload_id}/original',
                      base_url=ACME).status_code == 200


def test_uploads_are_public_unless_asked_otherwise(app, client, acme, globex, user):
    login_as(client, user)
    upload_id = _upload(client)

    assert app.test_client().get(f'/files/{upload_id}/original',
                                 base_url=ACME).status_code == 200


def test_a_members_only_file_is_not_cached_by_shared_caches(app, client, acme,
                                                            globex, user):
    """The visibility check only means something if a proxy in front respects
    it; send_file's max_age otherwise labels the response public."""
    login_as(client, user)
    members_id = _upload(client, 'members')
    public_id = _upload(client, 'public')

    members = client.get(f'/files/{members_id}/original', base_url=ACME)
    assert members.headers['Cache-Control'] == 'private, no-store'

    public = client.get(f'/files/{public_id}/original', base_url=ACME)
    assert 'public' in public.headers['Cache-Control']


def test_visibility_can_be_changed_after_upload(app, client, acme, globex, user):
    login_as(client, user)
    upload_id = _upload(client, 'members')

    client.post(f'/manage/media/{upload_id}', base_url=ACME,
                data={'visibility': 'public'})
    assert app.test_client().get(f'/files/{upload_id}/original',
                                 base_url=ACME).status_code == 200


def test_an_unknown_visibility_falls_back_to_public(app, client, acme, globex, user):
    login_as(client, user)
    bogus_id = _upload(client, 'nonsense')
    honoured_id = _upload(client, 'members')

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert db.session.get(Upload, bogus_id).visibility == 'public'
        # The same code path must still honour a real value, or the test above
        # would pass with the setting hardcoded.
        assert db.session.get(Upload, honoured_id).visibility == 'members'


def test_visibility_cannot_be_changed_from_another_tenants_host(app, client, acme,
                                                                globex, user):
    """The route loads by id, so it has to be unreachable from a host that
    resolves to a different organization."""
    login_as(client, user)
    upload_id = _upload(client, 'members')

    hank = User.query.filter_by(email='hank@example.com').one()
    other = app.test_client()
    login_as(other, hank)
    response = other.post(f'/manage/media/{upload_id}',
                          base_url='http://globex.example.test',
                          data={'visibility': 'public'})
    assert response.status_code == 404

    # Positive control: the same route works for a file Globex does own, so
    # the 404 above is the tenant filter and not a missing route.
    own_id = _upload(other, 'members', base_url='http://globex.example.test')
    assert other.post(f'/manage/media/{own_id}',
                      base_url='http://globex.example.test',
                      data={'visibility': 'public'}).status_code == 302

    # Acme's file is still members-only, and still refused to visitors.
    assert app.test_client().get(f'/files/{upload_id}/original',
                                 base_url=ACME).status_code == 404


def test_the_media_page_renders(app, client, acme, globex, user):
    """Nothing else in the suite fetches this page, so a template error in the
    per-file form would ship green."""
    login_as(client, user)
    assert client.get('/manage/media', base_url=ACME).status_code == 200

    _upload(client, 'members')
    response = client.get('/manage/media', base_url=ACME)
    assert response.status_code == 200
    assert b'name="visibility"' in response.data


def test_every_variant_of_a_members_only_file_is_gated(app, client, acme,
                                                       globex, user):
    """serve_upload rewrites the variant before serving, so a refactor that
    moved the check after that rewrite would still pass an original-only test."""
    login_as(client, user)
    upload_id = _upload(client, 'members')

    anonymous = app.test_client()
    for variant in ('original', 'thumb', 'medium', 'full'):
        assert anonymous.get(f'/files/{upload_id}/{variant}',
                             base_url=ACME).status_code == 404


def test_omitting_the_field_leaves_visibility_alone(app, client, acme,
                                                    globex, user):
    """On an update an absent field means no change. Defaulting it to public
    would let a truncated post quietly expose a members-only file."""
    login_as(client, user)
    upload_id = _upload(client, 'members')

    client.post(f'/manage/media/{upload_id}', base_url=ACME, data={})

    assert app.test_client().get(f'/files/{upload_id}/original',
                                 base_url=ACME).status_code == 404


def test_the_logo_picker_offers_only_public_images(app, client, acme,
                                                   globex, user):
    """The logo and favicon are rendered to visitors, so a members-only file
    chosen here is a broken image rather than a private one."""
    login_as(client, user)
    public_id = _upload(client, 'public')
    members_id = _upload(client, 'members')

    body = client.get('/manage/branding', base_url=ACME).data.decode()
    assert f'value="{public_id}"' in body
    assert f'value="{members_id}"' not in body


# --- foreign keys taken from a form --------------------------------------------------

def _globex_rows(app, globex):
    with app.test_request_context(base_url='http://globex.example.test'):
        g.org = globex
        upload = Upload(org_id=globex.id, key='org/2/x.png', filename='x.png',
                        content_type='image/png', size=1, visibility='public')
        page = Content(org_id=globex.id, type='page', title='Theirs',
                       slug='their-page', body='x', status='published')
        db.session.add_all([upload, page])
        db.session.commit()
        return upload.id, page.id


def test_a_navigation_link_cannot_point_at_another_tenants_page(app, client, acme,
                                                                globex, user):
    """A relationship load is exempt from the tenant filter, so an unchecked
    id here would render another organization's address in this one's menu.
    The foreign id is nulled, which leaves the link without a destination —
    so nothing is created at all."""
    _, foreign_page_id = _globex_rows(app, globex)
    login_as(client, user)

    client.post('/manage/navigation', base_url=ACME,
                data={'menu': 'primary', 'label': 'Foreign',
                      'content_id': str(foreign_page_id)})

    db.session.expire_all()
    item = NavigationItem.query.filter_by(org_id=acme.id, label='Foreign').first()
    assert item is None


def test_a_navigation_link_to_our_own_page_still_works(app, client, acme,
                                                       globex, user):
    login_as(client, user)
    ours = Content.query.filter_by(org_id=acme.id, type='article').first()

    client.post('/manage/navigation', base_url=ACME,
                data={'menu': 'primary', 'label': 'Ours',
                      'content_id': str(ours.id)})

    db.session.expire_all()
    item = NavigationItem.query.filter_by(org_id=acme.id, label='Ours').first()
    assert item.content_id == ours.id


def test_a_featured_image_cannot_be_another_tenants_upload(app, client, acme,
                                                           globex, user):
    foreign_upload_id, _ = _globex_rows(app, globex)
    login_as(client, user)
    page = Content.query.filter_by(org_id=acme.id, type='page').first()

    client.post(f'/manage/content/{page.id}/edit', base_url=ACME,
                data={'title': 'A', 'slug': page.slug, 'body': 'b',
                      'status': 'published', 'visibility': 'public',
                      'featured_upload_id': str(foreign_upload_id)})

    db.session.expire_all()
    assert db.session.get(Content, page.id).featured_upload_id is None


def test_a_logo_cannot_be_another_tenants_upload(app, client, acme, globex, user):
    foreign_upload_id, _ = _globex_rows(app, globex)
    login_as(client, user)

    client.post('/manage/branding', base_url=ACME,
                data={'name': 'Acme',
                      'logo_upload_id': str(foreign_upload_id)})

    db.session.expire_all()
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert (acme.setting('logo_upload_id') or None) is None


def test_a_favicon_cannot_be_another_tenants_upload(app, client, acme, globex, user):
    foreign_upload_id, _ = _globex_rows(app, globex)
    login_as(client, user)
    ours = _upload(client, 'public')

    # Ours is stored, so the refusal below cannot pass by the field simply
    # never being looked at.
    client.post('/manage/branding', base_url=ACME,
                data={'name': 'Acme',
                      'favicon_upload_id': str(ours)})
    db.session.expire_all()
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert acme.setting('favicon_upload_id') == ours

    client.post('/manage/branding', base_url=ACME,
                data={'name': 'Acme',
                      'favicon_upload_id': str(foreign_upload_id)})
    db.session.expire_all()
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert (acme.setting('favicon_upload_id') or None) is None


def test_uploading_a_featured_image_inline_creates_a_media_upload(
        app, client, acme, user):
    """The content form's file input creates a real media-library Upload and
    attaches it in the same save; the edit form then offers it as a
    thumbnail choice."""
    import io

    from PIL import Image

    from app.models import Upload
    login_as(client, user)
    article = Content.query.filter_by(org_id=acme.id, type='article').first()
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'purple').save(buffer, format='PNG')
    buffer.seek(0)

    client.post(f'/manage/content/{article.id}/edit', base_url=ACME,
                data={'title': 'A', 'slug': article.slug, 'body': 'b',
                      'visibility': 'public',
                      'featured_upload_file': (buffer, 'photo.png')},
                content_type='multipart/form-data')

    db.session.expire_all()
    saved = db.session.get(Content, article.id)
    assert saved.featured_upload_id is not None
    upload = db.session.get(Upload, saved.featured_upload_id)
    assert upload.org_id == acme.id
    assert upload.content_type == 'image/png'

    form = client.get(f'/manage/content/{article.id}/edit', base_url=ACME)
    assert b'featured_upload_file' in form.data
    assert f'value="{upload.id}" class="peer sr-only"\n                   checked'.encode() in form.data or \
        f'value="{upload.id}"'.encode() in form.data
