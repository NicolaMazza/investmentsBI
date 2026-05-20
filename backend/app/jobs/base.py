from __future__ import annotations

import datetime
import logging
from contextlib import contextmanager
from typing import Generator

from app.db.reporting import JobRun
from app.db.reporting_session import SessionLocal

log = logging.getLogger(__name__)


@contextmanager
def job_context(job_name: str) -> Generator[JobRun, None, None]:
    """Wraps a job run: writes job_run on entry and exit, never propagates to the scheduler."""
    session = SessionLocal()
    run = JobRun(
        job_name=job_name,
        started_at=datetime.datetime.now(datetime.timezone.utc),
        status="running",
    )
    session.add(run)
    session.commit()
    try:
        yield run
        run.status = "ok"
    except Exception as exc:
        run.status = "failed"
        run.message = str(exc)
        log.exception("Job %s failed", job_name)
    finally:
        run.finished_at = datetime.datetime.now(datetime.timezone.utc)
        session.add(run)
        session.commit()
        session.close()
