from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()

_JOBS: dict[str, object] = {}


def _load_jobs() -> dict[str, object]:
    global _JOBS
    if not _JOBS:
        from app.jobs import ishares_holdings
        _JOBS = {
            "ishares_holdings": ishares_holdings.run,
        }
    return _JOBS


@router.post("/admin/refresh")
def refresh(job: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    jobs = _load_jobs()
    if job not in jobs:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job '{job}'. Available: {sorted(jobs)}",
        )
    background_tasks.add_task(jobs[job])  # type: ignore[operator]
    log.info("Enqueued manual refresh for job '%s'", job)
    return {"status": "accepted", "job": job}
