"""`flask seed getsupremely` must work on a fresh install: it bootstraps the
Platform Admin the wizard would have created, then seeds the dogfood org."""

from app.models import Organization


def test_seed_bootstraps_fresh_install(app, runner):
    result = runner.invoke(args=['seed', 'getsupremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created: admin@supremely.org' in result.output
    assert 'One-time password:' in result.output
    assert 'Seeded organization "Supremely"' in result.output
    assert Organization.get_by_slug('getsupremely') is not None


def test_seed_is_idempotent(app, runner):
    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    result = runner.invoke(args=['seed', 'getsupremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created' not in result.output
    assert 'Seeded organization "Supremely"' in result.output


def test_seed_uses_existing_admin(app, runner, platform_admin):
    result = runner.invoke(args=['seed', 'getsupremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created' not in result.output
    org = Organization.get_by_slug('getsupremely')
    assert org.memberships[0].user_id == platform_admin.id


def test_reseeding_never_clobbers_owner_edits(app, runner):
    """Idempotent means heal, not reset: copy edited under Manage survives."""
    from flask import g

    from app.models import Organization
    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('getsupremely')
        g.org = org
        store = dict(org.setting('theme_content'))
        store['supremely'] = {**store['supremely'],
                              'headline_accent': 'my own words.'}
        org.update_settings(theme_content=store)
        org.description = 'Custom description'
        org.theme = 'midnight'
        org.save()

    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('getsupremely')
        assert org.theme == 'midnight'
        assert org.description == 'Custom description'
        content = org.setting('theme_content')['supremely']
        assert content['headline_accent'] == 'my own words.'


def test_seed_creates_the_get_page_and_ctas(app, runner):
    """The landing CTAs lead with the community and land on /get; the /get
    page itself is seeded and published."""
    from flask import g

    from app.models import Content, NavigationItem, Organization
    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('getsupremely')
        g.org = org
        get_page = Content.query.filter_by(type='page', slug='get').first()
        assert get_page is not None
        assert get_page.status == 'published'
        content = org.setting('theme_content')['supremely']
        assert content['primary_label'] == 'Explore the community'
        assert content['primary_url'] == '/discussions'
        assert content['secondary_label'] == 'Get Supremely'
        assert content['secondary_url'] == '/get'
        assert NavigationItem.query.filter_by(
            menu='footer', label='Get Supremely').count() == 1
        team = Content.query.filter_by(type='page', slug='team').first()
        assert team is not None and team.status == 'published'
        assert NavigationItem.query.filter_by(
            menu='footer', label='Team').count() == 1


def test_seed_creates_the_starter_forum(app, runner):
    from flask import g

    from app.models import DiscussionGroup, Organization, Post
    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    with app.test_request_context():
        org = Organization.get_by_slug('getsupremely')
        g.org = org
        names = [grp.name for grp in DiscussionGroup.query
                 .order_by(DiscussionGroup.position).all()]
        assert names == ['Welcome', 'General', 'Ideas & Feedback',
                         'Development']
        assert Post.query.count() == 6
    # Re-seeding neither duplicates groups nor posts.
    assert runner.invoke(args=['seed', 'getsupremely']).exit_code == 0
    with app.test_request_context():
        g.org = Organization.get_by_slug('getsupremely')
        assert DiscussionGroup.query.count() == 4
        assert Post.query.count() == 6
