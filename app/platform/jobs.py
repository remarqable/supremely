"""DB-backed job queue and worker. See blueprint/patterns/jobs.md.

Handlers must be idempotent: a crash after the work but before the status
write means the job runs again.
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.models.base import utcnow
from app.models.job import Job
from app.platform.logger import get_logger
from app.platform.tenant import org_scope

log = get_logger()

HANDLERS: dict[str, Callable] = {}

ZOMBIE_TIMEOUT = timedelta(minutes=15)


def job(name: str):
    """Register a job handler."""
    def register(fn):
        HANDLERS[name] = fn
        return fn
    return register


def enqueue(name: str, *, org_id=None, run_at=None, max_attempts: int = 3,
            **payload) -> Job:
    if name not in HANDLERS:
        raise ValueError(f'Unknown job: {name}')
    return Job(name=name, payload=payload, org_id=org_id,
               run_at=run_at or utcnow(), max_attempts=max_attempts).save()


def _claim_next() -> Job | None:
    """Atomically claim one due job. Portable two-step claim: candidate
    select, then a conditional UPDATE whose rowcount detects a lost race."""
    now = utcnow()
    candidate_q = (sa.select(Job.id)
                   .where(Job.status == 'pending', Job.run_at <= now)
                   .order_by(Job.run_at)
                   .limit(1))
    # On PostgreSQL, SKIP LOCKED lets concurrent workers each grab a different
    # head row instead of all contending on the same one. No-op on SQLite
    # (single writer anyway), where the rowcount re-check below is the guard.
    if current_app.config.get('IS_POSTGRES'):
        candidate_q = candidate_q.with_for_update(skip_locked=True)
    candidate = db.session.scalars(candidate_q).first()
    if candidate is None:
        db.session.rollback()
        return None

    result = db.session.execute(
        sa.update(Job)
        .where(Job.id == candidate, Job.status == 'pending')
        .values(status='running', locked_at=now, attempts=Job.attempts + 1)
    )
    db.session.commit()
    if result.rowcount != 1:
        return None                     # lost the race, safely
    return db.session.get(Job, candidate)


def _execute(job_row: Job) -> None:
    job_id = job_row.id
    # The scope covers the bookkeeping below as well as the handler. The
    # commit in the finally is a write like any other, and outside the
    # scope it was the one write the guard could not see: a handler that
    # left a row pending and did not commit had it written for it, with no
    # tenant in force. Job itself is not org scoped, so recording the
    # outcome here is unaffected by the filter.
    with org_scope(job_row.org_id):
        _run(job_row, job_id)


def _run(job_row: Job, job_id: int) -> None:
    try:
        HANDLERS[job_row.name](job_row.payload or {})
        # Flush what the handler left behind while we are still inside the
        # try, so a refusal fails this job rather than escaping through the
        # finally and stopping the worker.
        db.session.flush()
        job_row.status, job_row.finished_at = 'done', utcnow()
    except Exception as e:            # noqa: BLE001 -- worker must survive any handler error
        db.session.rollback()
        job_row = db.session.get(Job, job_id)
        if job_row is None:
            return                    # job was deleted mid-run; nothing to update
        log.error('job_failed', job=job_row.name, id=job_row.id, error=str(e))
        job_row.last_error = f'{type(e).__name__}: {e}'
        if job_row.attempts >= job_row.max_attempts:
            job_row.status = 'failed'
            # Record when it gave up: the admin list needs a time to show, and
            # _cleanup only purges rows that have one.
            job_row.finished_at = utcnow()
        else:
            job_row.status = 'pending'
            backoff = 30 * 4 ** max(job_row.attempts - 1, 0)
            job_row.run_at = utcnow() + timedelta(seconds=backoff)
    finally:
        if job_row is not None:
            job_row.locked_at = None
            db.session.add(job_row)
            db.session.commit()


def recover_zombies() -> int:
    """Reset 'running' jobs orphaned by a crashed worker. Covers rows whose
    locked_at is NULL (crash between the reset and its commit) as well as
    those locked before the timeout."""
    cutoff = utcnow() - ZOMBIE_TIMEOUT
    result = db.session.execute(
        sa.update(Job)
        .where(Job.status == 'running',
               sa.or_(Job.locked_at < cutoff, Job.locked_at.is_(None)))
        .values(status='pending', locked_at=None)
    )
    db.session.commit()
    return result.rowcount


DONE_RETENTION = timedelta(days=7)


def reschedule(name: str, run_at: datetime) -> None:
    """Book the next run of a recurring job, once.

    enqueue() commits, so a re-enqueue at the top of a handler survives the
    rollback when that handler then fails -- and the failed job is put back
    to pending with a backoff, which leaves two. Each of those books its
    own successor on the next failure, so the count doubles every time and
    so does how often the work runs. For a handler that talks to somebody
    else's server, that is a schedule that quietly turns into a flood.

    The guard is what makes "recurring" mean one, the same thing
    _seed_recurring_jobs assumes.
    """
    pending = db.session.scalar(
        sa.select(Job.id).where(Job.name == name,
                                Job.status == 'pending').limit(1))
    if pending is None:
        enqueue(name, run_at=run_at)


@job('system.update_check')
def _update_check(payload: dict) -> None:
    """Recurring: ask whether a newer image has been published.

    Rescheduled first, like the cleanup beside it, so a failed check cannot
    take the schedule down with it -- and this one talks to somebody else's
    server, which is the most likely thing here to fail.

    Daily. The answer changes when an image is pushed, nobody needs to hear
    about that within the hour, and asking more often would spend an
    installation's goodwill at a registry for nothing.
    """
    from app.platform import updates
    if not updates.enabled():
        # Switched off since this was booked. Not rescheduled, so the job
        # retires itself rather than waking daily to do nothing; the
        # worker books it again if it is ever switched back on.
        return
    reschedule('system.update_check', utcnow() + timedelta(days=1))
    if updates.built_at() is None:
        return          # a source build has no build to be behind
    # Not wrapped. Every network failure is already silence inside fetch,
    # so anything raising here is the database, which is the installation's
    # own and belongs in the failed queue where an admin can see it --
    # swallowing it would leave a check that has not worked for a month
    # looking exactly like one that has. The schedule is safe either way:
    # reschedule above books tomorrow whatever happens after it, and its
    # pending-guard is what stops a retry booking a second one.
    updates.record_check(updates.fetch_latest_built_at())


@job('system.cleanup')
def _cleanup(payload: dict) -> None:
    """Recurring: re-enqueue for tomorrow, then purge old finished jobs so the
    queue table stays a queue, not an archive."""
    reschedule('system.cleanup', utcnow() + timedelta(days=1))
    cutoff = utcnow() - DONE_RETENTION
    db.session.execute(
        sa.delete(Job).where(Job.status.in_(('done', 'failed')),
                             Job.finished_at.isnot(None),
                             Job.finished_at < cutoff))
    db.session.commit()


def _seed_recurring_jobs() -> None:
    """Ensure singleton recurring jobs exist. Idempotent."""
    recurring = ['system.cleanup']
    if current_app.config.get('UPDATE_CHECK_ENABLED'):
        # Nothing to book when the check is switched off: the handler would
        # wake daily only to return, and the queue would carry a job the
        # operator asked not to run.
        recurring.append('system.update_check')
    for name in recurring:
        exists = db.session.scalar(
            sa.select(Job.id).where(Job.name == name,
                                    Job.status == 'pending').limit(1))
        if not exists:
            enqueue(name)


def run_worker() -> None:
    """Blocking worker loop. Run as `flask jobs run`."""
    log.info('worker_started')
    recover_zombies()
    _seed_recurring_jobs()
    interval = current_app.config.get('JOBS_POLL_INTERVAL', 2)
    while True:
        job_row = _claim_next()
        if job_row is None:
            time.sleep(interval)
            continue
        _execute(job_row)


def run_pending_jobs(limit: int = 100) -> int:
    """Execute all due jobs inline. Test/CLI helper."""
    count = 0
    while count < limit:
        job_row = _claim_next()
        if job_row is None:
            break
        _execute(job_row)
        count += 1
    return count
