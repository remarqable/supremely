"""Installation logic: seed a fresh install and (for PostgreSQL) migrate the
chosen database. Lives in the platform layer, not the controller — the setup
route only parses input and calls these.

The PostgreSQL migration runs in a one-shot subprocess before the app is
switched over (the running process keeps serving the bootstrap SQLite DB until
restart); this is the single-migrator path, not an in-request upgrade of the
live database.
"""

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from flask import current_app

from app.models import InstallationSetting, Membership, Organization, User


def seed_installation(db_session, state: dict) -> User:
    """Create installation settings, the platform admin, and (optionally) the
    first organization with its starter content. Commits on success."""
    env = state['environment']
    admin_data = state['admin']

    admin_user = User(email=admin_data['email'],
                      name=admin_data['email'].split('@')[0],
                      is_platform_admin=True)
    admin_user.set_password(admin_data['password'])
    db_session.add(admin_user)
    db_session.flush()

    settings = {
        'installation.name': env['name'],
        'installation.base_url': env['base_url'],
        'installation.timezone': env['timezone'],
        'installation.language': env['language'],
        'installation.allow_organization_signups': 'false',
    }
    for key, value in (state.get('email') or {}).items():
        settings[f'email.{key}'] = value
    for key, value in settings.items():
        db_session.add(InstallationSetting(key=key, value=value))

    org_data = state.get('organization') or {}
    if org_data.get('slug'):
        org = Organization(name=org_data['name'], slug=org_data['slug'])
        db_session.add(org)
        db_session.flush()
        db_session.add(Membership(user_id=admin_user.id, org_id=org.id,
                                  role='owner'))
        from app.platform.defaults import seed_default_content
        seed_default_content(db_session, org, owner_id=admin_user.id)

    db_session.commit()
    return admin_user


def migrate_and_seed_postgres(url: str, state: dict) -> tuple[bool, str]:
    """Migrate the chosen PostgreSQL database in a one-shot subprocess, then
    seed it. Returns (ok, error_message)."""
    root = Path(current_app.root_path).parent
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'flask', 'db', 'upgrade'],
            cwd=root, capture_output=True, text=True, timeout=180,
            env={**os.environ, 'DATABASE_URL': url, 'FLASK_APP': 'wsgi.py'},
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout)[-500:]

        engine = sa.create_engine(url)
        try:
            with sa.orm.Session(engine) as pg_session:
                seed_installation(pg_session, state)
        finally:
            engine.dispose()
        return True, ''
    except Exception as e:      # noqa: BLE001 -- wizard must report, not crash
        return False, str(e)
