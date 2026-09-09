import pytest
from flask import g

from app import version_label
from app.extensions import db
from app.models import Content, NavigationItem
from app.platform.theming import page_template_exists
from tests.conftest import login_as, make_user


def publish_page(app, org, slug='story', title='Our Story', body='Our story.',
                 visibility='public', **kwargs):
    with app.test_request_context():
        g.org = org
        page = Content(type='page', title=title, slug=slug, body=body,
                       org_id=org.id, visibility=visibility, fields={}, tags=[],
                       **kwargs)
        page.save()
        page.publish()
        page_id = page.id
    db.session.expire_all()
    return page_id


ACME = 'http://acme.example.test'


def test_public_page_renders(app, client, acme, globex):
    publish_page(app, acme)
    response = client.get('/story', base_url=ACME)
    assert response.status_code == 200
    assert b'Our Story' in response.data
    assert b'Our story.' in response.data


def test_draft_page_404(app, client, acme, globex):
    with app.test_request_context():
        g.org = acme
        Content(type='page', title='Secret', slug='secret', org_id=acme.id, fields={}, tags=[]).save()
    assert client.get('/secret', base_url=ACME).status_code == 404


def test_unknown_page_404(client, acme, globex):
    assert client.get('/nope', base_url=ACME).status_code == 404


def test_pages_are_tenant_isolated(app, client, acme, globex):
    publish_page(app, acme, body='Acme story')
    publish_page(app, globex, body='Globex story')
    acme_page = client.get('/story', base_url=ACME)
    globex_page = client.get('/story', base_url='http://globex.example.test')
    assert b'Acme story' in acme_page.data
    assert b'Globex story' not in acme_page.data
    assert b'Globex story' in globex_page.data


def test_member_only_page_gates_anonymous(app, client, acme, globex):
    # Tease-don't-hide: a friendly 200 gate with the title and a login CTA,
    # never the body.
    publish_page(app, acme, slug='inside', visibility='members',
                 body='Secret handshake')
    response = client.get('/inside', base_url=ACME)
    assert response.status_code == 200
    assert b'Secret handshake' not in response.data
    assert b'Members only' in response.data
    assert b'/auth/login' in response.data


def test_member_only_page_visible_to_member(app, client, acme, globex, user):
    publish_page(app, acme, slug='inside', visibility='members')
    login_as(client, user)
    assert client.get('/inside', base_url=ACME).status_code == 200


def test_member_only_page_gated_for_non_member(app, client, acme, globex):
    publish_page(app, acme, slug='inside', visibility='members',
                 body='Secret handshake')
    outsider = make_user(email='out@example.com')
    login_as(client, outsider)
    # Logged in but not a member: same gate, no login CTA, never the body.
    response = client.get('/inside', base_url=ACME)
    assert response.status_code == 200
    assert b'Secret handshake' not in response.data
    assert b'Members only' in response.data


def publish_article(app, org, slug, title, body, visibility='public'):
    with app.test_request_context():
        g.org = org
        article = Content(type='article', title=title, slug=slug, body=body,
                          org_id=org.id,
                          visibility=visibility, fields={}, tags=[])
        article.save()
        article.publish()
        return article.permalink


def test_archive_teases_gated_items(app, client, acme, globex):
    """Members-only items appear in public archives as locked titles —
    excerpt and body withheld — and their permalink lands on the gate."""
    publish_article(app, acme, 'open', 'Open Article', 'Everyone reads this.')
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    listing = client.get('/blog', base_url=ACME)
    assert b'Open Article' in listing.data
    assert b'Everyone reads this.' in listing.data
    assert b'Closed Article' in listing.data          # title teased
    assert b'The inner circle' not in listing.data    # excerpt withheld
    assert b'Members only' in listing.data

    gate = client.get(permalink, base_url=ACME)
    assert gate.status_code == 200
    assert b'Closed Article' in gate.data
    assert b'The inner circle' not in gate.data
    assert b'/auth/login' in gate.data


def set_teasers(app, org, enabled):
    with app.test_request_context():
        g.org = org
        org.update_settings(gated_teasers=enabled)


def test_teasers_off_hides_gated_content(app, client, acme, globex):
    """With the org's tease switch off, gated items vanish from public
    lists and direct hits degrade to login redirect (anonymous) / 404
    (signed-in non-member) — the title never renders."""
    publish_article(app, acme, 'open', 'Open Article', 'Everyone reads this.')
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    set_teasers(app, acme, False)

    listing = client.get('/blog', base_url=ACME)
    assert b'Open Article' in listing.data
    assert b'Closed Article' not in listing.data

    response = client.get(permalink, base_url=ACME)
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']

    outsider = make_user(email='outsider@example.com')
    login_as(client, outsider)
    assert client.get(permalink, base_url=ACME).status_code == 404


def test_teasers_off_member_still_reads(app, client, acme, globex, user):
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    set_teasers(app, acme, False)
    login_as(client, user)
    page = client.get(permalink, base_url=ACME)
    assert page.status_code == 200
    assert b'The inner circle only.' in page.data


def test_manage_privacy_toggle(app, client, acme, globex, user):
    login_as(client, user)                       # acme's owner
    response = client.post('/manage/settings/privacy', base_url=ACME,
                           data={})                       # checkbox absent
    assert response.status_code == 302
    assert acme.teases_gated_content() is False
    client.post('/manage/settings/privacy', base_url=ACME,
                data={'gated_teasers': 'on'})
    assert acme.teases_gated_content() is True


def test_section_lock_gates_the_whole_section(app, client, acme, globex, user):
    """Manage → Content types lock: every item in the section gates for
    non-members, item visibility notwithstanding; members unaffected."""
    permalink = publish_article(app, acme, 'open', 'Open Article',
                                'Everyone reads this.')       # public item
    owner = app.test_client()
    login_as(owner, user)
    # One form per type now, carrying everything this organization has
    # decided about it, rather than a lock button of its own.
    response = owner.post('/manage/content-types/article', base_url=ACME,
                          data={'enabled': 'on', 'visibility': 'members',
                                'tease': 'inherit'})
    assert response.status_code == 302
    assert acme.type_visibility('article') == 'members'

    listing = client.get('/blog', base_url=ACME)
    assert listing.status_code == 200                 # one gate for the area
    assert b'Open Article' not in listing.data
    assert b'Members only' in listing.data
    single = client.get(permalink, base_url=ACME)
    assert b'Everyone reads this.' not in single.data
    assert b'Members only' in single.data

    member_view = owner.get('/blog', base_url=ACME)
    assert b'Open Article' in member_view.data

    # Back to public.
    owner.post('/manage/content-types/article', base_url=ACME,
               data={'enabled': 'on', 'visibility': 'public',
                     'tease': 'inherit'})
    assert acme.type_visibility('article') == 'public'
    assert b'Everyone reads this.' in client.get(permalink,
                                                 base_url=ACME).data


def test_a_type_with_nothing_to_decide_has_no_form(app, client, acme, globex,
                                                   user):
    """Pages cannot be turned off and have no archive to gate, so the
    console offers no controls for them and the route accepts none."""
    login_as(client, user)
    assert client.post('/manage/content-types/page', base_url=ACME,
                       data={'visibility': 'members'}).status_code == 404
    assert acme.type_visibility('page') == 'public'


def test_gated_single_readable_by_member(app, client, acme, globex, user):
    permalink = publish_article(app, acme, 'closed', 'Closed Article',
                                'The inner circle only.',
                                visibility='members')
    login_as(client, user)
    response = client.get(permalink, base_url=ACME)
    assert response.status_code == 200
    assert b'The inner circle only.' in response.data


def test_home_page_is_theme_hero(app, client, acme, globex):
    """The home page is the active theme's front page, edited as theme content
    (Manage → Home page) — not a CMS page. Origin renders an editable hero."""
    acme.update_settings(theme_content={'origin': {
        'headline': 'This is the home page.'}})
    response = client.get('/', base_url=ACME)
    assert b'This is the home page.' in response.data


def test_navigation_rendered(app, client, acme, globex):
    page_id = publish_page(app, acme)
    with app.test_request_context():
        g.org = acme
        NavigationItem(menu='primary', label='Story', content_id=page_id,
                       org_id=acme.id, position=99).save()
        NavigationItem(menu='footer', label='Imprint', url='https://x.test',
                       org_id=acme.id, position=99).save()
    response = client.get('/', base_url=ACME)
    assert b'href="/story"' in response.data
    assert b'https://x.test' in response.data


def test_theme_override_applies(app, client, acme, globex):
    acme.theme = 'midnight'
    acme.save()
    response = client.get('/', base_url=ACME)
    assert response.status_code == 200
    assert b'midnight' in response.data       # theme layout class
    assert b'theme.css' in response.data

    # Other org unaffected: presentation is per-tenant
    other = client.get('/', base_url='http://globex.example.test')
    assert b'midnight' not in other.data


def test_theme_asset_served(client, acme, globex):
    acme.theme = 'midnight'
    acme.save()
    response = client.get('/themes/midnight/static/theme.css', base_url=ACME)
    assert response.status_code == 200
    assert b'--midnight-accent' in response.data


def test_unknown_theme_asset_404(client, acme, globex):
    assert client.get('/themes/evil/static/x.css',
                      base_url=ACME).status_code == 404


# --- a page's template must not reach an application template ------------------------
ACME_HOST = 'http://acme.example.test'


@pytest.mark.parametrize('template', [
    'discussion-post',   # renders community/discussion-post.html, 500s on a page
    'discussion-group',
    'archive',
    'single',
    'members',
    'discussions',
    'newsletters',
])
def test_community_templates_are_not_offerable_as_a_page_template(app, acme, template):
    """render_site puts community/ ahead of every theme candidate, so picking
    one of these names renders the application's own page without the context
    it needs. Origin ships several of the same names, so merely existing in a
    theme is not enough to make a name safe."""
    with app.test_request_context(base_url=ACME_HOST):
        g.org = acme
        assert page_template_exists(template) is False


def test_every_offerable_template_renders_for_a_visitor(app, client, acme):
    """The half the model regex cannot see: whatever survives validation has
    to actually render on a public URL, for an anonymous visitor."""
    offerable = []
    with app.test_request_context(base_url=ACME_HOST):
        g.org = acme
        for name in ('page', 'front-page', 'gate', 'header', 'footer', 'layout',
                     'subscribe', 'confirm', 'unsubscribe'):
            if page_template_exists(name):
                offerable.append(name)
    assert offerable, 'expected the theme to offer at least one page template'

    page_id = publish_page(app, acme, slug='tmpl-render')
    for name in offerable:
        page = db.session.get(Content, page_id)
        page.template = name
        db.session.add(page)
        db.session.commit()
        response = client.get('/tmpl-render', base_url=ACME_HOST)
        assert response.status_code == 200, f'{name} returned {response.status_code}'


def test_a_template_the_theme_no_longer_provides_falls_back(app, client, acme):
    page_id = publish_page(app, acme, slug='tmpl-stale')
    page = db.session.get(Content, page_id)
    page.template = 'wide-legacy'
    db.session.add(page)
    db.session.commit()

    response = client.get('/tmpl-stale', base_url=ACME_HOST)
    assert response.status_code == 200
    assert b'Our story.' in response.data


def test_editor_refuses_a_template_the_theme_does_not_provide(app, client, acme, user):
    page_id = publish_page(app, acme, slug='tmpl-form')
    login_as(client, user)

    response = client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                           data={'title': 'T', 'slug': 'tmpl-form', 'body': 'b',
                                 'status': 'published', 'visibility': 'public',
                                 'template': 'discussion-post'},
                           follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).template is None
    assert b'No template called' in response.data


def test_editor_still_saves_a_page_carrying_a_stranded_template(app, client, acme,
                                                                user):
    """A theme switch can leave a name the theme no longer provides, and the
    form posts it straight back. That must not block editing the rest."""
    page_id = publish_page(app, acme, slug='tmpl-stranded')
    page = db.session.get(Content, page_id)
    page.template = 'wide-legacy'
    db.session.add(page)
    db.session.commit()

    login_as(client, user)
    client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed', 'slug': 'tmpl-stranded', 'body': 'b',
                      'status': 'published', 'visibility': 'public',
                      'template': 'wide-legacy'}, follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).title == 'Renamed'


def _store_raw_template(page_id, value):
    """Write the column directly, as a row saved before the rule existed."""
    db.session.execute(db.text('UPDATE content SET template=:t WHERE id=:i'),
                       {'t': value, 'i': page_id})
    db.session.commit()
    db.session.expire_all()


@pytest.mark.parametrize('stored', [
    'manage/members',       # rendered the Manage console to anonymous visitors
    'manage/domains',
    'discussion-post',      # rendered community/, and 500ed for want of context
    'archive',
    '../../../etc/passwd',
])
def test_a_row_stored_before_the_rule_is_ignored_at_render(app, client, acme, stored):
    """The write-time guard cannot reach rows that already exist, so the read
    side has to refuse them too."""
    page_id = publish_page(app, acme, slug='legacy-tmpl')
    _store_raw_template(page_id, stored)

    response = client.get('/legacy-tmpl', base_url=ACME_HOST)
    assert response.status_code == 200
    body = response.data.decode()
    assert 'Manage —' not in body
    assert 'csrf_token' not in body
    assert 'Our story.' in body          # its own body, via the default template


def test_editing_a_legacy_row_clears_the_bad_value(app, client, acme, user):
    page_id = publish_page(app, acme, slug='legacy-heal')
    _store_raw_template(page_id, 'discussion-post')
    login_as(client, user)

    client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed', 'slug': 'legacy-heal', 'body': 'b',
                      'status': 'published', 'visibility': 'public',
                      'template': 'discussion-post'}, follow_redirects=True)
    db.session.expire_all()
    page = db.session.get(Content, page_id)
    assert page.template is None      # dropped, not refused
    assert page.title == 'Renamed'    # and the rest of the edit went through


def test_a_newly_typed_disallowed_template_is_refused(app, client, acme, user):
    page_id = publish_page(app, acme, slug='legacy-new')
    login_as(client, user)

    response = client.post(f'/manage/content/{page_id}/edit', base_url=ACME_HOST,
                           data={'title': 'T', 'slug': 'legacy-new', 'body': 'b',
                                 'status': 'published', 'visibility': 'public',
                                 'template': 'members'}, follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Content, page_id).template is None
    assert b'No template called' in response.data


def test_a_non_page_row_with_a_legacy_template_is_still_editable(app, client,
                                                                 acme, user):
    """validate() enforces the rule for every content type, but only the page
    editor has a Template field. Without the clear-up, an article carrying a
    legacy value could never be saved again and there was no way to fix it."""
    article = Content.query.filter_by(org_id=acme.id, type='article').first()
    article.status = 'published'
    db.session.add(article)
    db.session.commit()
    article_id, slug = article.id, article.slug
    _store_raw_template(article_id, 'manage/members')

    login_as(client, user)
    client.post(f'/manage/content/{article_id}/edit', base_url=ACME_HOST,
                data={'title': 'Renamed Article', 'slug': slug, 'body': 'b',
                      'status': 'published', 'visibility': 'public'},
                follow_redirects=True)
    db.session.expire_all()
    row = db.session.get(Content, article_id)
    assert row.title == 'Renamed Article'
    assert row.template is None


def test_preview_ignores_a_legacy_template_too(app, client, acme, user):
    """Preview is the second reader of the same column."""
    page_id = publish_page(app, acme, slug='legacy-preview')
    _store_raw_template(page_id, 'manage/members')

    login_as(client, user)
    response = client.get(f'/manage/content/{page_id}/preview', base_url=ACME_HOST)
    assert response.status_code == 200
    assert b'Manage &mdash;' not in response.data
    assert 'Manage —' not in response.data.decode()


# --- the shop window ----------------------------------------------------------

def publish_episode(app, org, title, slug, visibility='public'):
    from app.models import Content
    with app.test_request_context(base_url=ACME):
        g.org = org
        item = Content(type='episode', title=title, slug=slug,
                       body='Show notes.', org_id=org.id, tags=[],
                       visibility=visibility,
                       fields={'audio_url': 'https://cdn.example.com/e.mp3'})
        item.save()
        item.publish()
        return item.permalink


def test_the_site_advertises_the_community_and_links_inward(app, client, acme,
                                                            user):
    """The acceptance: a visitor sees a section on the themed front page and
    clicking an item lands on the item's own address in the community.

    A list, never a second copy of the page: the site has no URL of its own
    for an episode, so there is nothing for a search engine to choose
    between.
    """
    permalink = publish_episode(app, acme, 'Episode one', 'episode-one')
    acme.set_type_settings('episode', site_entry=True)

    home = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Episode one' in home
    assert 'Latest Podcast' in home          # the section heading
    assert permalink in home                 # links to the community address

    landed = client.get(permalink, base_url=ACME)
    assert landed.status_code == 200
    assert b'Show notes.' in landed.data


def test_a_gated_item_is_locked_in_the_window_or_absent_from_it(app, client,
                                                                acme, user):
    """Teasing decides which. Not rebuilt here: the window reads through the
    same query an archive does, so the two cannot disagree about what a
    visitor may see listed."""
    publish_episode(app, acme, 'Members only episode', 'members-only',
                    visibility='members')
    acme.set_type_settings('episode', site_entry=True)

    acme.update_settings(gated_teasers=True)
    teased = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Members only episode' in teased      # the title, locked
    assert 'Show notes.' not in teased           # never the body

    acme.update_settings(gated_teasers=False)
    hidden = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Members only episode' not in hidden


def test_turning_a_section_off_removes_it_and_nothing_else(app, client, acme,
                                                           user):
    """The window is not the content. Taking a section off the front page
    leaves the archive, the item and the sidebar exactly as they were."""
    permalink = publish_episode(app, acme, 'Episode two', 'episode-two')
    acme.set_type_settings('episode', site_entry=True)
    assert 'Episode two' in client.get('/', base_url=ACME).get_data(as_text=True)

    acme.set_type_settings('episode', site_entry=False)

    home = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Episode two' not in home
    assert 'Latest Podcast' not in home
    # Everything else is untouched.
    assert client.get('/podcast', base_url=ACME).status_code == 200
    assert client.get(permalink, base_url=ACME).status_code == 200
    login_as(client, user)
    assert b'Podcast' in client.get('/dashboard', base_url=ACME).data


def test_a_disabled_type_has_nothing_to_advertise(app, client, acme, user):
    """Turning a type off is a stronger statement than taking its section
    off the front page, and has to imply it.

    Asserted on the list of sections and not only on the rendered page: an
    empty section draws nothing either way, so a page assertion alone passes
    whether or not this gate exists.
    """
    from app.platform.content_types import site_entry_types
    publish_episode(app, acme, 'Episode three', 'episode-three')
    acme.set_type_settings('episode', site_entry=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert 'episode' in [ct.slug for ct in site_entry_types()]

    acme.set_type_settings('episode', enabled=False)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert 'episode' not in [ct.slug for ct in site_entry_types()]

    home = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Episode three' not in home
    assert 'Latest Podcast' not in home


def test_a_locked_section_is_not_advertised_at_all(app, client, acme, user):
    """A section only members may read is not a heading on the public front
    page with nothing under it.

    The list itself has to leave it out. Leaving it in and relying on the
    section drawing no items would announce that something private exists,
    and would make the access decision a matter of whether a theme happens
    to skip empty sections. Themes are renderers.
    """
    from app.platform.content_types import site_entry_types
    publish_episode(app, acme, 'Episode four', 'episode-four')
    acme.set_type_settings('episode', site_entry=True, visibility='members')

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert 'episode' not in [ct.slug for ct in site_entry_types()]

    home = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'Latest Podcast' not in home
    assert 'Episode four' not in home

    login_as(client, user)
    assert 'Episode four' in client.get(
        '/', base_url=ACME).get_data(as_text=True)


# --- blocks on the public site -------------------------------------------------

def publish_course(app, org, title='Intro to Woodwork', slug='intro'):
    # Both are library types an organization opts into, so a test that wants
    # a course has to ask for one the same way an organization does.
    org.set_type_settings('course', enabled=True)
    org.set_type_settings('lesson', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = org
        course = Content(type='course', title=title, slug=slug,
                         body='What this course covers.', org_id=org.id,
                         tags=[], visibility='public', fields={})
        course.save()
        course.publish()
        return course


def add_block(app, org, parent, **kwargs):
    defaults = {'type': 'recipe', 'title': 'A recipe card', 'body': 'Mix it.',
                'org_id': org.id, 'tags': [], 'fields': {},
                'visibility': 'public', 'parent_id': parent.id}
    defaults.update(kwargs)
    with app.test_request_context(base_url=ACME):
        g.org = org
        child = Content(**defaults)
        child.save()
        child.publish()
        return child


def test_a_block_renders_inside_its_parent_and_nowhere_else(app, client, acme,
                                                            user):
    """The acceptance: a card added to an article is read below the article
    and appears in no archive, no feed and no count."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        acme.set_type_settings('recipe', enabled=True)
    article = publish_article(app, acme, 'host', 'Host article', 'The prose.')
    add_block(app, acme, Content.published_by_slug('article', 'host'),
              title='Sourdough card', body='Flour, water, salt.')

    page = client.get('/blog/host', base_url=ACME).get_data(as_text=True)
    assert 'The prose.' in page
    assert 'Sourdough card' in page          # below the body
    assert 'Flour, water, salt.' in page

    # ...and nowhere a standalone recipe would be.
    assert 'Sourdough card' not in client.get(
        '/recipes', base_url=ACME).get_data(as_text=True)
    assert 'Sourdough card' not in client.get(
        '/', base_url=ACME).get_data(as_text=True)
    assert client.get('/recipes/sourdough-card', base_url=ACME).status_code == 404
    assert article                            # the article itself is untouched


def test_a_gated_block_is_teased_or_hidden_like_anything_else(app, client,
                                                              acme, user):
    """A block decides its own visibility -- lesson one public, the rest
    members-only -- and listing it follows the organization's tease-or-hide
    switch, exactly as an archive does.

    Deciding it separately for blocks would make a course the one place in
    the product where gating means "vanish" while everywhere else it means
    "locked title", and would leave the lesson's own gate page with nothing
    linking to it.
    """
    course = publish_course(app, acme)
    add_block(app, acme, course, type='lesson', title='Lesson one', slug='one',
              body='Free preview.', visibility='public')
    add_block(app, acme, course, type='lesson', title='Lesson two', slug='two',
              body='Paid content.', visibility='members')

    acme.update_settings(gated_teasers=True)          # the default
    teased = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Free preview.' in teased
    assert 'Lesson two' in teased                     # the title, locked
    assert 'Paid content.' not in teased              # never the body

    acme.update_settings(gated_teasers=False)
    hidden = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Lesson one' in hidden
    assert 'Lesson two' not in hidden
    assert 'Paid content.' not in hidden

    login_as(client, user)
    for teasing in (True, False):
        acme.update_settings(gated_teasers=teasing)
        member_page = client.get('/courses/intro',
                                 base_url=ACME).get_data(as_text=True)
        assert 'Lesson two' in member_page, teasing
        assert 'Paid content.' in member_page, teasing


def test_a_routable_block_has_a_page_under_its_parent(app, client, acme, user):
    course = publish_course(app, acme)
    add_block(app, acme, course, type='lesson', title='Lesson one', slug='one',
              body='The first lesson.')

    landed = client.get('/courses/intro/lessons/one', base_url=ACME)
    assert landed.status_code == 200
    assert b'The first lesson.' in landed.data
    # The lesson is not also published beside its course.
    assert client.get('/lessons/one', base_url=ACME).status_code == 404


def test_a_block_under_a_gated_parent_is_not_reachable_by_its_address(app,
                                                                      client,
                                                                      acme,
                                                                      user):
    """Knowing the URL is not membership. The course gates the lesson
    whatever the lesson says about itself."""
    acme.set_type_settings('course', enabled=True)
    acme.set_type_settings('lesson', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        course = Content(type='course', title='Members course', slug='paid',
                         body='Members only.', org_id=acme.id, tags=[],
                         visibility='members', fields={})
        course.save()
        course.publish()
    add_block(app, acme, course, type='lesson', title='Lesson one', slug='one',
              body='Should not leak.', visibility='public')

    landed = client.get('/courses/paid/lessons/one', base_url=ACME)
    assert b'Should not leak.' not in landed.data

    login_as(client, user)
    assert b'Should not leak.' in client.get('/courses/paid/lessons/one',
                                             base_url=ACME).data


def test_the_child_route_does_not_swallow_application_urls(app, client, acme,
                                                           user):
    """A four-segment rule that matched anything would answer 404 where the
    application's own rule should answer."""
    login_as(client, user)
    # POST-only: the honest answer is "wrong method", not "no such page".
    assert client.get('/manage/content/article/preview',
                      base_url=ACME).status_code in (403, 405)


def test_the_reserved_slugs_cover_every_mounted_prefix(app):
    """The four-segment child route is kept off application paths by
    RESERVED_PAGE_SLUGS, so a blueprint mounted at a prefix that is not in
    that list would be silently swallowed: a GET to a POST-only URL under it
    would answer 404 instead of letting the real rule answer.

    The list is also what stops a page taking that slug, so the two uses
    agree by construction -- but only while it is complete.
    """
    from app.models.content import RESERVED_PAGE_SLUGS
    mounted = {rule.rule.strip('/').split('/')[0]
               for rule in app.url_map.iter_rules()}
    prefixes = {segment for segment in mounted
                if segment and not segment.startswith('<')}
    assert prefixes <= RESERVED_PAGE_SLUGS, sorted(prefixes - RESERVED_PAGE_SLUGS)


def test_blocks_render_inside_a_page_too(app, client, acme, user):
    """A page can hold blocks, so a page has to draw them. The editor offers
    the Add block form on any saved standalone row, pages included."""
    acme.set_type_settings('recipe', enabled=True)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='About us', slug='about-us',
                       body='Who we are.', org_id=acme.id, tags=[],
                       visibility='public', fields={})
        page.save()
        page.publish()
    add_block(app, acme, page, title='A card', body='Mix it.')

    body = client.get('/about-us', base_url=ACME).get_data(as_text=True)
    assert 'Who we are.' in body
    assert 'A card' in body
    assert 'Mix it.' in body


def test_a_block_is_gated_on_its_own_type_not_its_parents(app, client, acme,
                                                          user):
    """A course must not advertise a lesson that /lessons itself refuses to
    name.

    Listing a gated block follows the block's own type: its section lock and
    its own teasing switch. Asking the parent's type instead meant a course
    page teased a members-only lesson while the lesson archive gated the
    whole section and teased nothing, so the two surfaces disagreed about
    what a locked section means.
    """
    course = publish_course(app, acme)
    add_block(app, acme, course, type='lesson', title='Gated Lesson',
              slug='gated', body='Paid words.', visibility='members')
    acme.update_settings(gated_teasers=True)

    # The lesson section is locked outright: nothing of it is teased
    # anywhere, the course page included.
    acme.set_type_settings('lesson', visibility='members')
    locked = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Gated Lesson' not in locked
    assert 'Paid words.' not in locked
    # ...which is what the lesson's own archive does.
    assert 'Gated Lesson' not in client.get(
        '/lessons', base_url=ACME).get_data(as_text=True)

    # Section readable again, but this type says do not tease.
    acme.set_type_settings('lesson', visibility='public', tease=False)
    hidden = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Gated Lesson' not in hidden

    # ...and with its own type teasing, the title shows and the body does not.
    acme.set_type_settings('lesson', tease=True)
    teased = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Gated Lesson' in teased
    assert 'Paid words.' not in teased

    login_as(client, user)
    seen = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert 'Paid words.' in seen


def test_a_block_stops_linking_out_when_its_type_is_turned_off(app, client,
                                                               acme, user):
    """Turning a type off does not empty the articles that already use it as
    a block, but it does close the route. A block that kept its own address
    left a live link on every course page that answers 404."""
    course = publish_course(app, acme)
    add_block(app, acme, course, type='lesson', title='Lesson one', slug='one',
              body='The lesson.')
    linked = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert '/courses/intro/lessons/one' in linked

    acme.set_type_settings('lesson', enabled=False)
    page = client.get('/courses/intro', base_url=ACME).get_data(as_text=True)
    assert '/courses/intro/lessons/one' not in page
    assert 'Lesson one' in page              # still rendered in its parent
    assert client.get('/courses/intro/lessons/one',
                      base_url=ACME).status_code == 404


def test_the_community_sidebar_says_which_build_it_is(app, client, acme, user):
    """An operator on a server has no repository to ask and no git to run,
    and the image tag moves with every release. Without this there is no way
    to answer "which build is this" from the outside."""
    body = client.get('/blog', base_url=ACME).get_data(as_text=True)
    assert version_label() in body

    login_as(client, user)
    signed_in = client.get('/blog', base_url=ACME).get_data(as_text=True)
    assert version_label() in signed_in
