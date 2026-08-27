"""`flask seed demo`: a fresh install with preconfigured credentials."""

from flask import g

from app.models import Content, Membership, Organization, User


def test_demo_seed_is_a_standard_install_with_fixed_creds(app, runner):
    result = runner.invoke(args=['seed', 'demo'])
    assert result.exit_code == 0, result.output
    assert 'admin@demo.test / password' in result.output
    assert 'member@demo.test / password' in result.output

    with app.test_request_context():
        org = Organization.get_by_slug('demo')
        assert org is not None
        g.org = org

        admin = User.get_by_email('admin@demo.test')
        assert admin.check_password('password') and admin.is_platform_admin
        member = User.get_by_email('member@demo.test')
        assert member.check_password('password')
        assert not member.is_platform_admin
        assert Membership.query.filter_by(org_id=org.id).count() == 2

        # Nothing beyond the standard provisioning seeds — which now
        # demonstrate every library type on their own. Articles come as a
        # pair: one public, one members-only (the gating demonstration).
        for type_slug in ('event', 'announcement',
                          'recording', 'episode', 'resource'):
            assert Content.published_query(type_slug).count() == 1, type_slug
        assert Content.published_query('article').count() == 2
        assert Content.published_query('article').filter_by(
            visibility='members').count() == 1


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
