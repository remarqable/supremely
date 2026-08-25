"""Installation logic: seed a fresh install.

Lives in the platform layer, not the controller — the setup route only parses
input and calls this.

The wizard does not choose a database engine. Configuration resolves it before
the app boots (app/config.py): SQLite on the data volume by default, or
whatever DATABASE_URL points at when an operator sets one. The schema is
migrated before the first request, so seeding always targets the database
already in use.
"""

from app.models import InstallationSetting, Organization, User


def seed_installation(db_session, state: dict) -> User:
    """Create installation settings, the platform admin, and (optionally) the
    first organization with its starter content. Commits on success."""
    env = state['environment']
    admin_data = state['admin']
    org_data = state.get('organization') or {}

    # Validate the organization before anything is written: failing after the
    # admin is committed would leave an installation that cannot be retried,
    # because the admin would already exist.
    if org_data.get('slug'):
        Organization(name=org_data['name'], slug=org_data['slug']).validate()

    admin_user = User(email=admin_data['email'],
                      name=admin_data['email'],
                      is_platform_admin=True)
    admin_user.set_password(admin_data['password'])
    # This path adds to the session directly rather than going through
    # BaseModel.save(), so validate() has to be invoked explicitly or the
    # wizard is the one caller that can write an unvalidated user.
    admin_user.validate()
    db_session.add(admin_user)
    db_session.flush()

    settings = {
        'installation.name': env['name'],
        'installation.base_url': env['base_url'],
        'installation.timezone': env['timezone'],
        'installation.language': env['language'],
        'installation.allow_organization_signups': 'false',
    }
    for key, value in settings.items():
        db_session.add(InstallationSetting(key=key, value=value))

    db_session.commit()

    # Organization.provision is the canonical path (tenancy.md): it validates,
    # creates the owner membership and seeds starter content atomically.
    # Hand-rolling that here skipped validate() and let an over-length name
    # reach the column.
    if org_data.get('slug'):
        Organization.provision(name=org_data['name'], slug=org_data['slug'],
                               owner=admin_user)
    return admin_user
