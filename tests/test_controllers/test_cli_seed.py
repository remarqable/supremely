"""`flask seed supremely` must work on a fresh install: it bootstraps the
Platform Admin the wizard would have created, then seeds the dogfood org."""

from app.models import Organization


def test_seed_bootstraps_fresh_install(app, runner):
    result = runner.invoke(args=['seed', 'supremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created: admin@supremely.org' in result.output
    assert 'One-time password:' in result.output
    assert 'Seeded organization "Supremely"' in result.output
    assert Organization.get_by_slug('supremely') is not None


def test_seed_is_idempotent(app, runner):
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    result = runner.invoke(args=['seed', 'supremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created' not in result.output
    assert 'Seeded organization "Supremely"' in result.output


def test_seed_uses_existing_admin(app, runner, platform_admin):
    result = runner.invoke(args=['seed', 'supremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created' not in result.output
    org = Organization.get_by_slug('supremely')
    assert org.memberships[0].user_id == platform_admin.id


def test_reseeding_never_clobbers_owner_edits(app, runner):
    """Idempotent means heal, not reset: copy edited under Manage survives."""
    from flask import g

    from app.models import Organization
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('supremely')
        g.org = org
        store = dict(org.setting('theme_content'))
        store['supremely'] = {**store['supremely'],
                              'headline_accent': 'my own words.'}
        org.update_settings(theme_content=store)
        org.description = 'Custom description'
        org.theme = 'midnight'
        org.save()

    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('supremely')
        assert org.theme == 'midnight'
        assert org.description == 'Custom description'
        content = org.setting('theme_content')['supremely']
        assert content['headline_accent'] == 'my own words.'


def test_seed_creates_the_get_page_and_ctas(app, runner):
    """The landing CTAs lead with the community and land on /get; the /get
    page itself is seeded and published."""
    from flask import g

    from app.models import Content, NavigationItem, Organization
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('supremely')
        g.org = org
        get_page = Content.query.filter_by(type='page', slug='get').first()
        assert get_page is not None
        assert get_page.status == 'published'
        presskit = Content.query.filter_by(type='page', slug='presskit').first()
        assert presskit is not None and presskit.status == 'published'
        # The body carries the words; the asset grid lives in the theme's
        # page-presskit.html template.
        assert 'dev@supremely.org' in presskit.body
        assert 'remarQable LLC' in presskit.body
        content = org.setting('theme_content')['supremely']
        assert content['primary_label'] == 'Explore the community'
        assert content['primary_url'] == '/discussions'
        assert content['secondary_label'] == 'Get Supremely'
        assert content['secondary_url'] == '/get'
        assert NavigationItem.query.filter_by(
            menu='footer', label='Get Supremely').count() == 1
        # Footer link columns: four groups whose children the theme renders.
        for label, expected_children in (('Explore', 4), ('Project', 5),
                                         ('Developers', 5), ('Compare', 5)):
            group = NavigationItem.query.filter_by(
                menu='footer', label=label, parent_id=None).one()
            assert group.is_group
            assert len(group.children) == expected_children
        # Comparison pages and the manifesto are seeded and published.
        for slug in ('vs-ghost', 'vs-wordpress', 'vs-substack', 'vs-circle',
                     'vs-discourse', 'manifesto'):
            row = Content.query.filter_by(type='page', slug=slug).first()
            assert row is not None and row.status == 'published', slug


def test_seed_creates_the_team_roster(app, runner):
    """Team members are structured content (team_member type) with photos in
    the media library; any old team *page* retires because the type's
    archive owns /team. Re-running never duplicates members or uploads."""
    from flask import g

    from app.models import Content, NavigationItem, Organization, Upload
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        g.org = Organization.get_by_slug('supremely')
        members = Content.query.filter_by(type='team_member').all()
        assert {m.title for m in members} == {
            'Asim Baig', 'Sara Rasch', 'Aidan Urbina', 'Claudius Coddington'}
        assert all(m.status == 'published' for m in members)
        assert all(m.fields.get('role') for m in members)
        assert all(m.featured_upload_id for m in members)
        assert Content.query.filter_by(type='page', slug='team').first() is None
        team_link = NavigationItem.query.filter_by(
            menu='footer', label='Team').one()
        assert team_link.url == '/team' and team_link.content_id is None
        upload_count = Upload.query.count()

    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        g.org = Organization.get_by_slug('supremely')
        assert Content.query.filter_by(type='team_member').count() == 4
        assert Upload.query.count() == upload_count


def test_seed_creates_the_starter_forum(app, runner):
    from flask import g

    from app.models import DiscussionGroup, Organization, Post
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('supremely')
        g.org = org
        names = [grp.name for grp in DiscussionGroup.query
                 .order_by(DiscussionGroup.position).all()]
        assert names == ['Welcome', 'General', 'Ideas & Feedback',
                         'Development']
        assert Post.query.count() == 6
    # Re-seeding neither duplicates groups nor posts.
    assert runner.invoke(args=['seed', 'supremely']).exit_code == 0
    with app.test_request_context():
        g.org = Organization.get_by_slug('supremely')
        assert DiscussionGroup.query.count() == 4
        assert Post.query.count() == 6


def test_seed_adopts_org_under_legacy_slug(app, runner, platform_admin):
    """Installs seeded before the slug rename keep their organization: the
    seed renames it in place instead of provisioning a second one."""
    legacy = Organization.provision(name='Supremely', slug='getsupremely',
                                    owner=platform_admin, seed_defaults=False)
    legacy_id = legacy.id
    before = Organization.query.count()
    result = runner.invoke(args=['seed', 'supremely'])
    assert result.exit_code == 0, result.output
    assert Organization.get_by_slug('getsupremely') is None
    org = Organization.get_by_slug('supremely')
    assert org is not None and org.id == legacy_id
    assert Organization.query.count() == before
