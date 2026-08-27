"""`flask seed demo`: one command to a ready-to-click community."""

from flask import g

from app.models import Content, Membership, Organization, User
from app.models.discussion import Post, Reaction, Reply


def test_demo_seed_builds_the_showroom(app, runner):
    result = runner.invoke(args=['seed', 'demo'])
    assert result.exit_code == 0, result.output
    assert 'admin@demo.test / password' in result.output

    with app.test_request_context():
        org = Organization.get_by_slug('demo')
        assert org is not None
        g.org = org

        admin = User.get_by_email('admin@demo.test')
        assert admin.check_password('password')
        assert admin.is_platform_admin

        maya = User.get_by_email('maya@demo.test')
        assert maya.check_password('password')
        assert maya.bio

        assert Membership.query.filter_by(org_id=org.id).count() == 5

        # Forum liveliness on top of the starter seeds.
        assert Reply.query.count() >= 3
        assert Reaction.query.count() >= 3
        intro = Post.query.filter_by(title='Introduce yourself').one()
        assert intro.reply_count >= 2
        assert Post.query.filter_by(
            title="What I'm building this month").count() == 1

        # One published item per library type; provisioning covers the rest.
        for type_slug in ('recording', 'episode', 'resource',
                          'article', 'event', 'announcement'):
            assert Content.published_query(type_slug).count() >= 1, type_slug


def test_demo_seed_refuses_rerun_and_production(app, runner):
    assert runner.invoke(args=['seed', 'demo']).exit_code == 0
    rerun = runner.invoke(args=['seed', 'demo'])
    assert rerun.exit_code != 0
    assert 'make demo' in rerun.output

    app.config['APP_ENV'] = 'production'
    try:
        result = runner.invoke(args=['seed', 'demo'])
        assert result.exit_code != 0
        assert 'production' in result.output
    finally:
        app.config['APP_ENV'] = 'test'
