# Ghostfolio database adapter — pinned to Ghostfolio 3.3.0
# Ghostfolio tables live in the public schema of the ghostfolio database.
# On Ghostfolio upgrades, verify these models against the live schema before upgrading.
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class GhostfolioBase(DeclarativeBase):
    pass


# Models are populated in M3 once the Ghostfolio schema is mapped.
