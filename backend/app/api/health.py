from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.db.reporting_session import engine as reporting_engine
from app.db.ghostfolio_session import engine as ghostfolio_engine

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, object]:
    status: dict[str, object] = {"service": "investmentsbi", "version": "0.1.0"}

    for name, eng in [("reporting", reporting_engine), ("ghostfolio", ghostfolio_engine)]:
        try:
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            status[f"db_{name}"] = "ok"
        except Exception as exc:
            log.warning("DB check failed for %s: %s", name, exc)
            status[f"db_{name}"] = "error"

    status["healthy"] = all(v == "ok" for k, v in status.items() if k.startswith("db_"))
    return status
