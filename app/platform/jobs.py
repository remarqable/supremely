"""DB-backed job queue and worker. See blueprint/patterns/jobs.md.

Handlers must be idempotent: a crash after the work but before the status
write means the job runs again.
"""

import time
from datetime import timedelta
from typing import Callable, Optional

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.models.base import utcnow
from app.models.job import Job
from app.platform.logger import get_logger

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


def _claim_next() -> Optional[Job]:
    """Atomically claim one due job. Portable two-step claim: candidate
    select, then a conditional UPDATE whose rowcount detects a lost race."""
    now = utcnow()
    candidate = db.session.scalars(
        sa.select(Job.id)
        .where(Job.status == 'pending', Job.run_at <= now)
        .order_by(Job.run_at)
        .limit(1)
    ).first()
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
    try:
        HANDLERS[job_row.name](job_row.payload or {})
        job_row.status, job_row.finished_at = 'done', utcnow()
    except Exception as e:            # noqa: BLE001 -- worker must survive any handler error
        db.session.rollback()
        job_row = db.session.get(Job, job_row.id)
        log.error('job_failed', job=job_row.name, id=job_row.id, error=str(e))
        job_row.last_error = f'{type(e).__name__}: {e}'
        if job_row.attempts >= job_row.max_attempts:
            job_row.status = 'failed'
        else:
            job_row.status = 'pending'
            backoff = 30 * 4 ** max(job_row.attempts - 1, 0)
            job_row.run_at = utcnow() + timedelta(seconds=backoff)
    finally:
        job_row.locked_at = None
        db.session.add(job_row)
        db.session.commit()


def recover_zombies() -> int:
    """Reset 'running' jobs orphaned by a crashed worker."""
    cutoff = utcnow() - ZOMBIE_TIMEOUT
    result = db.session.execute(
        sa.update(Job)
        .where(Job.status == 'running', Job.locked_at < cutoff)
        .values(status='pending', locked_at=None)
    )
    db.session.commit()
    return result.rowcount


def run_worker() -> None:
    """Blocking worker loop. Run as `flask jobs run`."""
    log.info('worker_started')
    recover_zombies()
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
