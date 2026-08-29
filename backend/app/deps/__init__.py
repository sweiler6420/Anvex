"""Reusable FastAPI dependencies (``CLAUDE.md`` §3).

Dependencies wire objects together; they never implement logic. ANV-11 adds
``get_current_user`` and the service factories alongside these.
"""

from app.deps.session import get_session
from app.deps.settings import get_settings_dep

__all__ = ["get_session", "get_settings_dep"]
