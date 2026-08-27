"""Server-side CLI commands.

`flask users reset-password EMAIL` is the email-independent account recovery
path (auth: password). `flask jobs run` is the worker process.
"""

import secrets

import click
from flask import Blueprint, current_app

from app.extensions import db
from app.models import User

users_bp = Blueprint('users_cli', __name__, cli_group='users')
jobs_bp = Blueprint('jobs_cli', __name__, cli_group='jobs')
setup_bp = Blueprint('setup_cli', __name__, cli_group='setup')
seed_bp = Blueprint('seed_cli', __name__, cli_group='seed')
dev_bp = Blueprint('dev_cli', __name__, cli_group='dev')

CLI_BLUEPRINTS = (users_bp, jobs_bp, setup_bp, seed_bp, dev_bp)


@users_bp.cli.command('reset-password')
@click.argument('email')
def reset_password(email: str):
    """Set a new random password for EMAIL and print it once."""
    user = User.get_by_email(email.strip().lower())
    if user is None:
        raise click.ClickException(f'No user with email {email}')

    password = secrets.token_urlsafe(12)
    user.set_password(password)
    user.save()
    click.echo(f'New password for {user.email}: {password}')
    click.echo('It is shown only once. The user should change it after login.')


@users_bp.cli.command('create-admin')
@click.argument('email')
@click.option('--name', default=None)
def create_admin(email: str, name: str | None):
    """Create (or promote) a Platform Admin and print a one-time password."""
    email = email.strip().lower()
    user = User.get_by_email(email)
    if user:
        user.is_platform_admin = True
        user.save()
        click.echo(f'{user.email} is now a Platform Admin.')
        return
    password = secrets.token_urlsafe(12)
    user = User.create(email=email, name=name or email.split('@')[0],
                       password=password, is_platform_admin=True)
    click.echo(f'Platform Admin created: {user.email}')
    click.echo(f'One-time password: {password}')


@users_bp.cli.command('list')
def list_users():
    for user in User.query.order_by(User.email).all():
        badge = ' [platform-admin]' if user.is_platform_admin else ''
        click.echo(f'{user.id}\t{user.email}\t{user.name}{badge}')


@jobs_bp.cli.command('run')
def run_worker_command():
    """Run the background worker (blocking)."""
    from app.platform.jobs import run_worker
    run_worker()


@jobs_bp.cli.command('work-off')
def work_off():
    """Execute all currently due jobs, then exit."""
    from app.platform.jobs import run_pending_jobs
    count = run_pending_jobs()
    click.echo(f'Executed {count} job(s).')


@setup_bp.cli.command('reset')
def reset_wizard():
    """Re-enable the first-run wizard (server-side explicit reset)."""
    from app.platform.config_store import write_runtime_config
    write_runtime_config(current_app, {'SETUP_COMPLETE': 'false'})
    current_app.config['SETUP_COMPLETE'] = False
    click.echo('Setup wizard re-enabled. Restart the app if it is running.')


@dev_bp.cli.command('sync-db')
def sync_db():
    """Dev only: build the schema directly from the models (db.create_all) and
    stamp Alembic at head, so heavy iteration needs no migrations. Adds missing
    tables/indexes; it does NOT alter or drop existing ones — run `make reset`
    for an incompatible model change. Migrations remain the source of truth for
    prod/CI/smoke (`flask db upgrade`); regenerate the baseline at a milestone.
    """
    from flask_migrate import stamp

    import app.models  # noqa: F401  (populate db.metadata for create_all)

    db.create_all()
    try:
        stamp()                 # so a later `flask db upgrade` is a clean no-op
    except Exception as exc:    # noqa: BLE001 -- a missing migrations dir shouldn't block dev
        click.echo(f'(schema built; alembic stamp skipped: {exc})')
        return
    click.echo('Schema synced from models (create_all) and stamped at head.')


@seed_bp.cli.command('demo')
def seed_demo_command():
    """Dev only: seed a ready-to-click Demo Community with fixed
    credentials (everything is 'password'). Run via `make demo`, which
    wipes local data first. Refuses to run in production."""
    from app.platform.demo_seed import ADMIN_EMAIL, MEMBERS, PASSWORD, seed_demo
    try:
        seed_demo()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('Demo Community is ready: http://localhost:8000')
    click.echo(f'  owner   {ADMIN_EMAIL} / {PASSWORD}')
    for email, name, _bio in MEMBERS:
        click.echo(f'  member  {email} / {PASSWORD}   ({name})')
    click.echo('Fixed credentials: this seed refuses to run in production.')


@seed_bp.cli.command('getsupremely')
@click.option('--admin-email', default='admin@getsupremely.org',
              show_default=True,
              help='Platform Admin to create if none exists yet.')
def seed_getsupremely(admin_email: str):
    """Dogfood: build the Supremely project website as an Organization on
    this installation (spec Phase 9). Idempotent.

    On a fresh install (post `make reset`) this bootstraps what the setup
    wizard would have created: a Platform Admin (one-time password printed)
    and the installed marker — no wizard click-through needed."""
    from app.platform.config_store import installation_ready, mark_installed
    from app.platform.seed import seed_getsupremely_org

    if User.query.filter_by(is_platform_admin=True).first() is None:
        email = admin_email.strip().lower()
        password = secrets.token_urlsafe(12)
        user = User.get_by_email(email)
        if user is not None:
            user.is_platform_admin = True
            user.save()
            click.echo(f'{user.email} promoted to Platform Admin.')
        else:
            user = User.create(email=email, name=email.split('@')[0],
                               password=password, is_platform_admin=True)
            click.echo(f'Platform Admin created: {user.email}')
            click.echo(f'One-time password: {password}')
            click.echo('It is shown only once. Change it after login.')

    if not installation_ready(current_app):
        mark_installed(current_app)
        click.echo('Installation marked ready (setup wizard skipped).')

    org = seed_getsupremely_org()
    click.echo(f'Seeded organization "{org.name}" ({org.slug}).')
    click.echo('Visit it on the bare domain (single org) or its subdomain.')
