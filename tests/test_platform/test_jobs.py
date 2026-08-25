import pytest

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
