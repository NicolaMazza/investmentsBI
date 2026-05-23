from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.logging_config import setup_logging

setup_logging()

from app.api.admin import router as admin_router
from app.api.allocation import router as allocation_router
from app.api.drill import router as drill_router
from app.api.health import router as health_router
from app.api.timeseries import router as timeseries_router
from app.scheduler import shutdown, start


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    start()
    yield
    shutdown()


app = FastAPI(title="InvestmentsBI", version="0.1.0", lifespan=lifespan)

app.include_router(health_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(allocation_router, prefix="/api")
app.include_router(drill_router, prefix="/api")
app.include_router(timeseries_router, prefix="/api")

_frontend = Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
