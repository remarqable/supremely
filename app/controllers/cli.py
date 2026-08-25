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

CLI_BLUEPRINTS = (users_bp, jobs_bp, setup_bp, seed_bp)


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


@seed_bp.cli.command('getsupremely')
def seed_getsupremely():
    """Dogfood: build the Supremely project website as an Organization on
    this installation (spec Phase 9). Idempotent."""
    from app.platform.seed import seed_getsupremely_org
    org = seed_getsupremely_org()
    click.echo(f'Seeded organization "{org.name}" ({org.slug}).')
    click.echo('Visit it on the bare domain (single org) or its subdomain.')
