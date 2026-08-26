"""`flask seed getsupremely` must work on a fresh install: it bootstraps the
Platform Admin the wizard would have created, then seeds the dogfood org."""

from app.models import Organization


def test_seed_bootstraps_fresh_install(app, runner):
    result = runner.invoke(args=['seed', 'getsupremely'])
    assert result.exit_code == 0, result.output
    assert 'Platform Admin created: admin@getsupremely.org' in result.output
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
