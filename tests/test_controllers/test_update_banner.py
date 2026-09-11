"""Who is told the installation has fallen behind.

Updating is the operator's job. An organization owner on a shared host
cannot pull an image, so a banner telling them to would be a worry they can
do nothing with -- and a member should never see the machinery at all.

Every test here builds its own application, because whether the check is on
and when the image was built are configuration, and configuration is passed
into create_app rather than assigned onto a running one. The cost is that
the shared fixtures cannot be used: two apps are two in-memory databases,
so each one makes its own people.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import InstallationSetting, Membership, Organization
from app.platform import updates
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
INSTALL = 'http://example.test'
BEHIND = 'days behind'


def _app(tmp_path, *, built_days_ago: int):
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)
        UPDATE_CHECK_ENABLED = True
        APP_BUILT_AT = (
            datetime.now(UTC) - timedelta(days=built_days_ago)).isoformat()

    return create_app(Cfg)


@pytest.fixture
def behind(tmp_path):
    """An installation thirty days behind the published image."""
    app = _app(tmp_path, built_days_ago=30)
    with app.app_context():
        db.create_all()
        InstallationSetting.set(updates.LATEST_KEY,
                                datetime.now(UTC).isoformat())
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def _operator(email='root@example.com'):
    return make_user(email=email, name='Root', is_platform_admin=True)


def _page(app, user, path, base_url):
    client = login_as(app.test_client(), user)
    return client.get(path, base_url=base_url,
                      follow_redirects=True).get_data(as_text=True)


def test_the_operator_is_told(behind):
    assert BEHIND in _page(behind, _operator(), '/admin/', INSTALL)


def test_somebody_running_their_own_box_is_told_without_hunting_for_it(behind):
    """The common self-hosted shape: one person who both runs the server
    and owns the community on it. They may never open /admin, so the
    console they do use says it too."""
    sara = _operator(email='sara@example.com')
    Organization.provision(name='Acme', slug='acme', owner=sara)
    db.session.commit()

    assert BEHIND in _page(behind, sara, '/manage/members', ACME)


def test_an_organization_owner_is_not(behind):
    """They cannot update the installation. On a shared host they are a
    customer, and this is somebody else's server."""
    owner = make_user(email='owner@example.com', name='Owner')
    Organization.provision(name='Acme', slug='acme', owner=owner)
    db.session.commit()

    assert BEHIND not in _page(behind, owner, '/manage/members', ACME)


def test_a_member_never_sees_it(behind):
    owner = make_user(email='owner2@example.com', name='Owner')
    acme = Organization.provision(name='Acme', slug='acme', owner=owner)
    member = make_user(email='ada@example.com', name='Ada')
    Membership.add(member.id, acme.id, role='member')
    db.session.commit()

    assert BEHIND not in _page(behind, member, '/', ACME)


def test_a_visitor_never_sees_it(behind):
    owner = make_user(email='owner3@example.com', name='Owner')
    Organization.provision(name='Acme', slug='acme', owner=owner)
    db.session.commit()

    page = behind.test_client().get('/', base_url=ACME).get_data(as_text=True)
    assert BEHIND not in page


def test_nothing_is_said_when_the_installation_is_current(tmp_path):
    app = _app(tmp_path, built_days_ago=0)
    with app.app_context():
        db.create_all()
        InstallationSetting.set(updates.LATEST_KEY,
                                datetime.now(UTC).isoformat())
        db.session.commit()

        assert BEHIND not in _page(app, _operator(), '/admin/', INSTALL)
        db.session.remove()
        db.drop_all()


def test_a_broken_check_never_breaks_the_page(behind, monkeypatch):
    """A banner is not worth a 500. Anything unexpected in the check is
    nothing to report to somebody trying to use the console."""
    monkeypatch.setattr(updates, 'pending_update',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    client = login_as(behind.test_client(), _operator())

    response = client.get('/admin/', base_url=INSTALL)
    assert response.status_code == 200
    assert BEHIND not in response.get_data(as_text=True)


def test_the_banner_carries_what_the_browser_needs_to_remember_it(behind):
    """Dismissal is kept client-side, so there is no schema change and no
    migration -- and the key moves with the gap, so dismissing one does not
    hide the next."""
    page = _page(behind, _operator(), '/admin/', INSTALL)

    with behind.test_request_context():
        token = updates.pending_update()['token']
    assert f'supremely.update.{token}' in page
    assert 'localStorage' in page
