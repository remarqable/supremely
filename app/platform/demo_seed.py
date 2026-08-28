"""`flask seed demo`: a fresh install with preconfigured credentials.

Exactly what a new community gets from the standard provisioning path —
nothing more — plus a wizard bypass and fixed logins, so a development
machine reaches a testable state in one `make demo`. The feature-
demonstrating seed content itself lives in the provisioner
(app/platform/defaults.py) and ships to every real install too.

Fixed, guessable credentials (everything is `password`), so this refuses
to run in production, belt to the suspenders that production runs the
Docker image, whose entrypoint never invokes make. The model otherwise
refuses the passwords guessed first; this asks for the one exemption, and
that exemption is itself ignored in production, so it cannot travel.
"""

from flask import current_app

from app.platform.logger import get_logger

log = get_logger()

ADMIN_EMAIL = 'admin@demo.test'
MEMBER_EMAIL = 'member@demo.test'
PASSWORD = 'password'


def seed_demo() -> dict:
    from app.models import Membership, Organization, User
    from app.platform.config_store import mark_installed

    if current_app.config.get('APP_ENV') == 'production':
        raise RuntimeError('seed demo uses fixed credentials and refuses '
                           'to run in production.')
    if Organization.get_by_slug('demo') is not None:
        raise RuntimeError("The 'demo' community already exists — "
                           "run `make demo` for a fresh one.")

    admin = User.get_by_email(ADMIN_EMAIL) or User.create(
        email=ADMIN_EMAIL, name='Admin', password=PASSWORD,
        is_platform_admin=True, allow_common=True)
    mark_installed(current_app)

    org = Organization.provision(name='Demo Community', slug='demo',
                                 owner=admin)

    member = User.create(email=MEMBER_EMAIL, name='Demo Member',
                         password=PASSWORD, allow_common=True)
    member.bio = 'A regular member account for testing the member view.'
    member.save()
    Membership.add(member.id, org.id, role='member')

    log.info('seeded_demo', org_id=org.id)
    return {'org': org, 'admin': admin, 'member': member}
