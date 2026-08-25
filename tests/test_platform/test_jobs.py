import pytest

from app.extensions import db
from app.models import Job
from app.platform import jobs as jobs_module
from app.platform.jobs import enqueue, job, run_pending_jobs


@pytest.fixture(autouse=True)
def clean_handlers():
    saved = dict(jobs_module.HANDLERS)
    yield
    jobs_module.HANDLERS.clear()
    jobs_module.HANDLERS.update(saved)


def test_enqueue_and_run(app):
    results = []

    @job('test.echo')
    def echo(payload):
        results.append(payload['value'])

    enqueue('test.echo', value=42)
    assert run_pending_jobs() == 1
    assert results == [42]
    assert Job.query.filter_by(status='done').count() == 1


def test_unknown_job_rejected(app):
    with pytest.raises(ValueError):
        enqueue('test.does_not_exist')


def test_backoff_moves_run_at_forward(app):
    from app.models.base import utcnow

    @job('test.always_fails')
    def boom(payload):
        raise RuntimeError('nope')

    enqueue('test.always_fails', max_attempts=3)
    before = Job.query.first().run_at
    run_pending_jobs()
    row = Job.query.first()
    assert row.status == 'pending'
    assert row.run_at > before          # backoff scheduled it later


def test_cleanup_reenqueues_and_purges(app):
    from datetime import timedelta
    from app.models.base import utcnow
    from app.platform.jobs import DONE_RETENTION

    # An old finished job that should be purged
    old = Job(name='x', status='done',
              finished_at=utcnow() - DONE_RETENTION - timedelta(days=1))
    db.session.add(old)
    # A recent finished job that should survive
    recent = Job(name='y', status='done', finished_at=utcnow())
    db.session.add(recent)
    db.session.commit()
    old_id, recent_id = old.id, recent.id

    enqueue('system.cleanup')
    run_pending_jobs(limit=1)

    assert db.session.get(Job, old_id) is None
    assert db.session.get(Job, recent_id) is not None
    # It re-enqueued itself for tomorrow
    assert Job.query.filter_by(name='system.cleanup', status='pending').count() == 1


def test_zombie_recovery_covers_null_locked_at(app):
    from app.platform.jobs import recover_zombies
    stuck = Job(name='x', status='running', locked_at=None)
    db.session.add(stuck)
    db.session.commit()
    assert recover_zombies() == 1
    assert db.session.get(Job, stuck.id).status == 'pending'


def test_failing_job_retries_then_fails(app):
    attempts = []

    @job('test.boom')
    def boom(payload):
        attempts.append(1)
        raise RuntimeError('boom')

    enqueue('test.boom', max_attempts=2)
    run_pending_jobs()
    row = Job.query.first()
    assert row.status == 'pending'          # retry scheduled with backoff
    assert row.attempts == 1
    assert 'boom' in row.last_error

    row.run_at = row.created_at             # make it due now
    from app.extensions import db
    db.session.commit()
    run_pending_jobs()
    row = Job.query.first()
    assert row.status == 'failed'           # terminal after max_attempts
    assert len(attempts) == 2
