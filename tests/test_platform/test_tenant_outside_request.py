"""The tenant filter applies to work that has no request behind it.

Isolation rested on a filter that only ran during a request. A background
job has no request, so every handler ran with no filter at all: it could
read every tenant's rows and write to any of them, and neither the read
filter nor the cross-tenant write guard fired. Nothing shipped exploited
it, because no handler took an identifier from a visitor, but the guard was
absent rather than satisfied.

A job now runs under the organization it was queued for. Work that
genuinely spans tenants is queued without one and behaves as before.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Content
from app.models.newsletter import Subscriber
from app.platform.jobs import HANDLERS, enqueue, run_pending_jobs
from app.platform.tenant import current_org_id, org_scope


@pytest.fixture
def two_orgs(app, acme, globex):
    return acme, globex


@pytest.fixture
def probe():
    """A handler that reports what the tenant filter let it see."""
    seen = {}

    def handler(payload):
        seen['read'] = sorted({c.org_id for c in Content.query.all()})
        seen['org'] = current_org_id()

    HANDLERS['probe.tenant'] = handler
    yield seen
    HANDLERS.pop('probe.tenant', None)


def test_a_job_sees_only_the_tenant_it_was_queued_for(app, two_orgs, probe):
    acme, globex = two_orgs
    everything = sorted({c.org_id for c in Content.query.all()})
    assert len(everything) == 2, 'both tenants should have content to confuse'

    enqueue('probe.tenant', org_id=acme.id)
    run_pending_jobs()
    assert probe['read'] == [acme.id]

    enqueue('probe.tenant', org_id=globex.id)
    run_pending_jobs()
    assert probe['read'] == [globex.id]


def test_a_job_queued_without_a_tenant_still_spans_them(app, two_orgs, probe):
    """Housekeeping that is meant to cross tenants keeps working."""
    enqueue('probe.tenant', org_id=None)
    run_pending_jobs()
    assert len(probe['read']) == 2
    assert probe['org'] is None


def test_a_job_cannot_read_another_tenants_row(app, two_orgs):
    acme, globex = two_orgs
    foreign_id = Content.query.filter_by(org_id=globex.id).first().id
    db.session.expire_all()          # force a real query, not the identity map
    seen = {}

    def handler(payload):
        seen['row'] = db.session.get(Content, foreign_id)

    HANDLERS['probe.read'] = handler
    try:
        enqueue('probe.read', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.read', None)

    assert seen['row'] is None


def test_a_job_cannot_write_to_another_tenants_row(app, two_orgs):
    """A row already in the session can still be reached by primary key,
    so reading is not the only guard that has to hold."""
    acme, globex = two_orgs
    foreign = Content.query.filter_by(org_id=globex.id).first()
    foreign_id, before = foreign.id, foreign.title

    def handler(payload):
        row = db.session.get(Content, foreign_id)
        if row is not None:
            row.title = 'taken over'
            db.session.commit()

    HANDLERS['probe.write'] = handler
    try:
        enqueue('probe.write', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.write', None)

    db.session.expire_all()
    assert db.session.get(Content, foreign_id).title == before


def test_org_scope_restores_what_it_found(app, acme):
    assert current_org_id() is None
    with org_scope(acme.id):
        assert current_org_id() == acme.id
        with org_scope(None):
            assert current_org_id() is None
        assert current_org_id() == acme.id
    assert current_org_id() is None


def test_subscribing_does_not_reach_another_tenants_row(app, two_orgs):
    """Outside a request this returned the other organization's row, put it
    back to subscribed, and handed it to the caller."""
    acme, globex = two_orgs
    theirs = Subscriber(email='shared@example.test', org_id=acme.id,
                        status='unsubscribed', token='t1')
    db.session.add(theirs)
    db.session.commit()

    got = Subscriber.subscribe('shared@example.test', globex.id, False)

    assert got.id != theirs.id
    assert got.org_id == globex.id
    assert db.session.get(Subscriber, theirs.id).status == 'unsubscribed'


def test_the_audience_of_a_send_is_one_tenants_list(app, two_orgs):
    acme, globex = two_orgs
    for org in (acme, globex):
        db.session.add(Subscriber(email=f'reader@{org.slug}.test', org_id=org.id,
                                  status='subscribed',
                                  token=f'tok-{org.slug}'))
    db.session.commit()

    assert Subscriber.audience(acme.id).count() == 1
    assert Subscriber.audience(globex.id).count() == 1


def test_a_term_does_not_clash_with_another_communitys(app, two_orgs):
    from plugins.glossary.v1.models import GlossaryTerm

    acme, globex = two_orgs
    db.session.add(GlossaryTerm(term='Widget', definition='theirs',
                                org_id=acme.id))
    db.session.commit()

    mine = GlossaryTerm(term='Widget', definition='mine', org_id=globex.id)
    mine.validate()


def test_a_request_is_unaffected(app, acme, client):
    """The filter still keys off the request when there is one."""
    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        assert current_org_id() == acme.id
        assert {c.org_id for c in Content.query.all()} == {acme.id}


def test_reading_the_tenant_never_queries_for_it(app, acme):
    """The filter runs inside the query listener, so answering the question
    it asks must not ask the database. An expired organization answering
    for its own id issues a refresh, which re-enters the listener: once for
    the cost, and if it is expired again, until the stack runs out.
    """
    import sqlalchemy as sa

    statements = []

    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)

    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        db.session.rollback()          # this is what expires it
        sa.event.listen(db.engine, 'before_cursor_execute', record)
        try:
            rows = Content.query.limit(3).all()
        finally:
            sa.event.remove(db.engine, 'before_cursor_execute', record)

        assert not [s for s in statements if 'FROM organization' in s], (
            'the tenant was fetched to find out which tenant it is')
        assert {row.org_id for row in rows} == {acme.id}


def test_the_tenant_still_reads_when_its_row_is_detached(app, acme):
    """A detached instance used to raise from inside the listener, which
    surfaces as a failure with no obvious connection to tenancy."""
    org_id = acme.id                  # read before detaching, not after
    with app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        db.session.expunge(acme)
        assert current_org_id() == org_id
        Content.query.limit(1).all()


def test_a_job_that_leaves_a_write_pending_does_not_get_it_written_anyway(
        app, two_orgs):
    """The worker commits after the handler returns. That commit is a write
    like any other, and it used to happen after the tenant stopped being in
    force, so forgetting to commit was enough to slip a row past the guard.
    """
    acme, globex = two_orgs

    def handler(payload):
        db.session.add(Content(type='post', title='t', slug='left-pending',
                               status='draft', org_id=globex.id))
        # deliberately no commit

    HANDLERS['probe.pending'] = handler
    try:
        enqueue('probe.pending', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.pending', None)

    db.session.expire_all()
    assert Content.query.filter_by(slug='left-pending').first() is None


def test_a_job_that_leaves_an_edit_pending_does_not_get_it_written_anyway(
        app, two_orgs):
    acme, globex = two_orgs
    victim = Content.query.filter_by(org_id=globex.id).first()
    victim_id, before = victim.id, victim.title

    def handler(payload):
        row = db.session.get(Content, victim_id)
        if row is not None:
            row.title = 'taken over'
        # deliberately no commit

    HANDLERS['probe.pending_edit'] = handler
    try:
        enqueue('probe.pending_edit', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.pending_edit', None)

    db.session.expire_all()
    assert db.session.get(Content, victim_id).title == before


def test_the_worker_survives_a_refused_write(app, two_orgs):
    """The refusal has to fail the job, not stop the loop."""
    acme, globex = two_orgs
    ran = []

    def bad(payload):
        db.session.add(Content(type='post', title='t', slug='bad-one',
                               status='draft', org_id=globex.id))

    def good(payload):
        ran.append(True)

    HANDLERS['probe.bad'] = bad
    HANDLERS['probe.good'] = good
    try:
        enqueue('probe.bad', org_id=acme.id)
        enqueue('probe.good', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.bad', None)
        HANDLERS.pop('probe.good', None)

    assert ran == [True], 'the worker stopped after the refusal'
    from app.models.job import Job
    # Not 'failed': the first attempt of three goes back to pending for a
    # retry. What matters is that the refusal is what stopped it.
    refused = Job.query.filter_by(name='probe.bad').first()
    assert 'Refusing to write across tenants' in (refused.last_error or ''), (
        'the write was not refused, so this proves nothing about surviving one')


def test_a_handler_that_pushes_a_request_context_stays_scoped(app, two_orgs):
    """Reaching for a request context inside a handler is ordinary: it is
    how you build an absolute link or render a template that reads request.
    There is no organization resolved in one, so answering from the request
    alone would quietly discard the one the job was queued for.
    """
    acme, globex = two_orgs
    seen = {}

    def handler(payload):
        with app.test_request_context():
            seen['org'] = current_org_id()
            seen['read'] = sorted({c.org_id for c in Content.query.all()})
            db.session.add(Content(type='post', title='t',
                                   slug='pushed-context', status='draft',
                                   org_id=globex.id))

    HANDLERS['probe.pushed'] = handler
    try:
        enqueue('probe.pushed', org_id=acme.id)
        run_pending_jobs()
    finally:
        HANDLERS.pop('probe.pushed', None)

    assert seen['org'] == acme.id
    assert seen['read'] == [acme.id]
    db.session.expire_all()
    assert Content.query.filter_by(slug='pushed-context').first() is None


def test_a_resolved_request_still_wins_over_the_ambient(app, two_orgs):
    """Precedence has to hold, or a job could override the tenant of a
    request that resolved its own."""
    acme, globex = two_orgs
    with org_scope(globex.id), \
            app.test_request_context(base_url='http://acme.example.test'):
        g.org = acme
        assert current_org_id() == acme.id
        assert {c.org_id for c in Content.query.all()} == {acme.id}
