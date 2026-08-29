"""Settings as a FastAPI dependency."""

from __future__ import annotations

from app.settings import Settings, get_settings


def get_settings_dep() -> Settings:
    """Return the process-wide :class:`Settings` for injection.

    Exists so handlers write ``Depends(get_settings_dep)`` instead of calling
    ``get_settings()`` directly — which means a test can override the dependency instead
    of patching a cache. The lookup itself is ``lru_cache``d, so this is free.
    """
    return get_settings()


__all__ = ["Settings", "get_settings_dep"]
