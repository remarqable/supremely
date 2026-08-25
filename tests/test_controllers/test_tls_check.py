"""Caddy's on-demand TLS gate.

Wrong answers here are not cosmetic: a permissive gate lets a stranger burn
the installation's ACME rate limit, and a too-strict one leaves organizations
without certificates.
"""

from app.models import Organization
from app.models.domain import OrgDomain


def ask(client, host):
    return client.get('/tls-check', query_string={'domain': host})


def test_installation_domain_is_allowed(client):
    # TestConfig's BASE_DOMAIN is example.test
    assert ask(client, 'example.test').status_code == 204


def test_installation_domain_allowed_before_setup(app, client):
    """A fresh install must get its certificate before the wizard has run."""
    previous = app.config['SETUP_COMPLETE']
    app.config['SETUP_COMPLETE'] = False
    try:
        assert ask(client, 'example.test').status_code == 204
    finally:
        app.config['SETUP_COMPLETE'] = previous


def test_active_org_subdomain_is_allowed(client, acme):
    assert ask(client, f'{acme.slug}.example.test').status_code == 204


def test_unknown_subdomain_is_denied(client):
    assert ask(client, 'nobody.example.test').status_code == 403


def test_inactive_org_subdomain_is_denied(client, acme):
    acme.is_active = False
    acme.save()
    assert ask(client, f'{acme.slug}.example.test').status_code == 403


def test_reserved_slug_is_denied(client):
    reserved = sorted(Organization.RESERVED_SLUGS)[0]
    assert ask(client, f'{reserved}.example.test').status_code == 403


def test_nested_subdomain_is_denied(client, acme):
    assert ask(client, f'a.{acme.slug}.example.test').status_code == 403


def test_unrelated_host_is_denied(client):
    assert ask(client, 'attacker.example.com').status_code == 403


def test_active_custom_domain_is_allowed(app, client, acme):
    OrgDomain(org_id=acme.id, domain='blog.example.com', status='active').save()
    assert ask(client, 'blog.example.com').status_code == 204


def test_pending_custom_domain_is_denied(app, client, acme):
    OrgDomain(org_id=acme.id, domain='blog.example.com', status='pending').save()
    assert ask(client, 'blog.example.com').status_code == 403


def test_missing_domain_is_denied(client):
    assert client.get('/tls-check').status_code == 403


def test_port_and_case_are_normalised(client, acme):
    assert ask(client, f'{acme.slug.upper()}.EXAMPLE.TEST:443').status_code == 204


def test_suspended_org_custom_domain_is_denied(app, client, acme):
    """A suspended organization serves nothing, so nothing should be
    provisioned for it -- including a certificate on its custom domain.
    Renewing certificates for a host that can only ever answer 410 burns the
    ACME quota this endpoint exists to protect."""
    OrgDomain(org_id=acme.id, domain='blog.example.com', status='active').save()
    acme.is_active = False
    acme.save()
    assert ask(client, 'blog.example.com').status_code == 403


def test_second_org_subdomain_is_allowed(client, acme, globex):
    """With two organizations the bare domain stops resolving and both are
    served from subdomains; each still needs its own certificate."""
    assert ask(client, f'{acme.slug}.example.test').status_code == 204
    assert ask(client, f'{globex.slug}.example.test').status_code == 204


def test_one_orgs_custom_domain_does_not_authorise_another(app, client, acme, globex):
    OrgDomain(org_id=acme.id, domain='acme-blog.example.com',
              status='active').save()
    assert ask(client, 'acme-blog.example.com').status_code == 204
    assert ask(client, 'globex-blog.example.com').status_code == 403


def test_trailing_dot_is_normalised(client, acme):
    assert ask(client, f'{acme.slug}.example.test.').status_code == 204
