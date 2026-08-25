"""The tests that matter most: prove the tenant filter holds."""

from app.extensions import db
from app.models import Membership
from tests.conftest import login_as, make_user
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
