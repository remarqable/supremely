"""Shared test fixtures."""

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Membership, Organization, User

# Imported for its side effect: registers an OrgScoped model so tenant
# isolation is testable before the first real scoped business model exists.
from tests.scoped_probe import ScopedProbe  # noqa: F401


@pytest.fixture
def app(tmp_path):
    """Application for testing. Config is passed INTO create_app, never
    assigned afterwards: Flask-SQLAlchemy binds its engine during init_app.
    DATA_DIR is a tmp dir so uploads and installed themes never touch the
    real data volume."""
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


PASSWORD = 'correct-horse-9'


def make_user(email='user@example.com', name='Test User', **kwargs) -> User:
    return User.create(email=email, name=name, password=PASSWORD, **kwargs)


@pytest.fixture
def user(app):
    return make_user()


@pytest.fixture
def platform_admin(app):
    return make_user(email='root@example.com', name='Root',
                     is_platform_admin=True)


@pytest.fixture
def acme(app, user):
    return Organization.provision(name='Acme', slug='acme', owner=user)


@pytest.fixture
def globex(app):
    owner = make_user(email='hank@example.com', name='Hank')
    return Organization.provision(name='Globex', slug='globex', owner=owner)


def login_as(client, user):
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
    return client


@pytest.fixture
def admin_client(client, platform_admin):
    return login_as(client, platform_admin)


def make_png(width=600, height=400, color=(120, 90, 200)) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, 'PNG')
    return buf.getvalue()
