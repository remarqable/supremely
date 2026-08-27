"""The tests that matter most: prove the tenant filter holds."""

from app.extensions import db
from tests.scoped_probe import ScopedProbe


def seed_probe(org_id, name):
    probe = ScopedProbe(org_id=org_id, name=name)
    db.session.add(probe)
    db.session.commit()
    return probe


def test_tenant_cannot_read_other_tenants_rows(app, client, acme, globex, user):
    secret = seed_probe(globex.id, 'globex-secret')
    seed_probe(acme.id, 'acme-a')
    secret_id, acme_id = secret.id, acme.id
    db.session.expunge_all()    # force real SQL: get() checks the identity map first

    # Simulate a request-scoped query on acme's subdomain.
    with app.test_request_context(base_url='http://acme.example.test'):
        from flask import g

        from app.models import Organization
        g.org = db.session.get(Organization, acme_id)
        rows = ScopedProbe.query.all()
        assert [r.name for r in rows] == ['acme-a']
        # Primary-key lookup on another tenant's row (classic IDOR): None.
        assert db.session.get(ScopedProbe, secret_id) is None


def test_anonymous_requests_are_scoped_too(app, acme, globex):
    """PUBLIC_TENANTS: g.org set from the host before any auth decision."""
    seed_probe(acme.id, 'acme-public')
    seed_probe(globex.id, 'globex-secret')

    with app.test_request_context(base_url='http://acme.example.test'):
        from flask import g
        g.org = acme                    # what resolve_tenant does for anonymous
        names = [r.name for r in ScopedProbe.query.all()]
        assert names == ['acme-public']


def test_write_without_tenant_refused(app, acme):
    import pytest
    with app.test_request_context(base_url='http://example.test'):
        from flask import g
        g.org = None
        db.session.add(ScopedProbe(name='orphan'))
        with pytest.raises(RuntimeError, match='without a tenant'):
            db.session.flush()
        db.session.rollback()


def test_write_across_tenants_refused(app, acme, globex):
    import pytest
    with app.test_request_context(base_url='http://acme.example.test'):
        from flask import g
        g.org = acme
        db.session.add(ScopedProbe(org_id=globex.id, name='smuggled'))
        with pytest.raises(RuntimeError, match='across tenants'):
            db.session.flush()
        db.session.rollback()


def test_unscoped_escape_hatch(app, acme, globex):
    seed_probe(acme.id, 'a')
    seed_probe(globex.id, 'b')
    from app.platform.tenant import unscoped
    with app.test_request_context(base_url='http://acme.example.test'):
        from flask import g
        g.org = acme
        with unscoped():
            assert ScopedProbe.query.count() == 2
        assert ScopedProbe.query.count() == 1


def test_loopback_ip_serves_default_org_when_base_is_localhost(tmp_path):
    """http://127.0.0.1:8000 must behave like http://localhost:8000 in dev:
    a loopback IP is the same machine, not a foreign custom domain."""
    from app import create_app
    from app.config import TestConfig
    from app.models import Organization
    from tests.conftest import make_user

    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)
        BASE_DOMAIN = 'localhost'
        SERVER_NAME = 'localhost'

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        try:
            owner = make_user()
            Organization.provision(name='Solo', slug='solo', owner=owner)
            client = app.test_client()
            assert client.get('/', base_url='http://localhost').status_code == 200
            assert client.get('/', base_url='http://127.0.0.1').status_code == 200
            assert client.get('/', base_url='http://[::1]').status_code == 200
        finally:
            db.session.remove()
            db.drop_all()


def test_loopback_ip_stays_foreign_for_real_base_domains(client, acme):
    # BASE_DOMAIN is example.test here: a loopback IP is NOT the bare domain
    # and must keep resolving as an (unknown) custom domain.
    assert client.get('/', base_url='http://127.0.0.1').status_code == 404


# --- the guards around the filter ----------------------------------------------------

def test_a_row_from_another_tenant_cannot_be_deleted(app, acme, globex):
    """The read filter puts another tenant's row out of reach, but a row that
    did get into the session must not be removed either."""
    import pytest
    from flask import g

    from app.platform.tenant import unscoped

    with app.test_request_context():
        g.org = globex
        probe = seed_probe(globex.id, 'globex-row')
        probe_id = probe.id

    with app.test_request_context():
        g.org = acme
        with unscoped():
            smuggled = db.session.get(ScopedProbe, probe_id)
        db.session.delete(smuggled)
        with pytest.raises(RuntimeError, match='across tenants'):
            db.session.commit()
        db.session.rollback()

    with app.test_request_context():
        g.org = globex
        assert db.session.get(ScopedProbe, probe_id) is not None


def test_an_installation_prefix_stops_at_a_path_boundary(app):
    """'/admin' also matched '/adminfoo', so a page a tenant can legitimately
    publish skipped tenant resolution entirely."""
    from app.platform.tenant import is_installation_path

    for path in ('/adminfoo', '/setup-guide', '/launcherfoo', '/administrivia'):
        assert is_installation_path(path) is False, path

    for path in ('/admin', '/admin/', '/admin/orgs', '/setup', '/setup/',
                 '/launcher', '/launcher/new', '/auth/login', '/static/app.css',
                 '/health'):
        assert is_installation_path(path) is True, path


def test_the_host_is_normalised_before_it_picks_a_tenant(app, acme):
    """Host names are case-insensitive and may carry a trailing root dot.
    org_for_host already normalised; this did not, so the two disagreed."""
    from werkzeug.test import EnvironBuilder

    from app.platform.tenant import _request_host

    cases = {
        'ACME.example.test': 'acme.example.test',
        'Acme.Example.Test:8000': 'acme.example.test',
        'acme.example.test.': 'acme.example.test',
        '[::1]:8000': '::1',
        '[acme.example': '',          # malformed: resolve no tenant
    }
    for sent, expected in cases.items():
        with app.request_context(EnvironBuilder(headers={'Host': sent}).get_environ()):
            assert _request_host() == expected, sent


def test_a_slug_check_outside_a_request_stays_in_its_own_org(app, acme, globex):
    """Seeding and the CLI run with no request, where the session filter is
    off: an unpinned lookup then spans every tenant, so a name another
    organization already uses reads back as a collision."""
    import pytest
    from flask import g

    from app.models import Category, Content
    from app.models.discussion import DiscussionGroup
    from app.platform.errors import ValidationError

    with app.test_request_context():
        g.org = globex
        db.session.add_all([
            DiscussionGroup(org_id=globex.id, name='Shared',
                            slug='shared-name', visibility='public'),
            Category(org_id=globex.id, name='Shared', slug='shared-name'),
            Content(org_id=globex.id, type='page', title='Shared',
                    slug='shared-name', body='x'),
        ])
        db.session.commit()

    # No request context at all: the same names must be free for another org.
    DiscussionGroup(org_id=acme.id, name='Shared', slug='shared-name',
                    visibility='public').validate()
    Category(org_id=acme.id, name='Shared', slug='shared-name').validate()
    Content(org_id=acme.id, type='page', title='Shared', slug='shared-name',
            body='x').validate()

    # And a duplicate inside one organization is still refused, or the checks
    # above would pass with the uniqueness rule simply deleted.
    with app.test_request_context():
        g.org = globex
        for row in (DiscussionGroup(org_id=globex.id, name='Again',
                                    slug='shared-name', visibility='public'),
                    Category(org_id=globex.id, name='Again',
                             slug='shared-name'),
                    Content(org_id=globex.id, type='page', title='Again',
                            slug='shared-name', body='x')):
            with pytest.raises(ValidationError, match='already exists'):
                row.validate()


def test_nulling_org_id_does_not_get_a_row_past_the_write_guard(app, acme, globex):
    """Anyone able to put a foreign row in the session can also clear its
    org_id, so the guard has to read the value the row was loaded with."""
    import pytest
    from flask import g

    from app.platform.tenant import unscoped

    with app.test_request_context():
        g.org = globex
        probe = seed_probe(globex.id, 'theirs')
        probe_id = probe.id

    for change in ('delete', 'update'):
        with app.test_request_context():
            g.org = acme
            with unscoped():
                smuggled = db.session.get(ScopedProbe, probe_id)
            smuggled.org_id = None
            if change == 'delete':
                db.session.delete(smuggled)
            else:
                smuggled.name = 'rewritten'
            with pytest.raises(RuntimeError, match='across tenants'):
                db.session.commit()
            db.session.rollback()

    with app.test_request_context():
        g.org = globex
        survivor = db.session.get(ScopedProbe, probe_id)
        assert survivor is not None and survivor.name == 'theirs'
