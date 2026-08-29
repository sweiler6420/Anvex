"""Tests for the application factory itself: lifespan, wiring and the schema rule."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI

from app import main
from app.settings import Settings


class TestFactory:
    def test_create_app_returns_a_fresh_instance_each_time(self, settings: Settings) -> None:
        """Isolation matters: a dependency override in one test must not reach another."""
        first = main.create_app(settings)
        second = main.create_app(settings)
        assert isinstance(first, FastAPI)
        assert first is not second

    def test_module_level_app_exists_for_uvicorn(self) -> None:
        """`uv run uvicorn app.main:app` and the compose healthcheck import this name."""
        assert isinstance(main.app, FastAPI)

    def test_both_routers_are_mounted(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ANV-11 onward only has to include a router in `app/api/v1/__init__.py`.

        Asserted by watching what `create_app` mounts rather than by inspecting paths,
        because the v1 router legitimately has no routes yet.
        """
        from app.api import health_router, v1_router

        included: list[object] = []
        original = FastAPI.include_router

        def spy(self: FastAPI, router: object, *args: object, **kwargs: object) -> None:
            included.append(router)
            return original(self, router, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(FastAPI, "include_router", spy)
        main.create_app(settings)

        assert health_router in included
        assert v1_router in included

    def test_the_version_prefix_lives_only_on_the_v1_router(self) -> None:
        """`CLAUDE.md` §4: the prefix carries the version, path decorators never do."""
        from app.api import v1_router

        assert v1_router.prefix == "/v1"


class TestLifespan:
    async def test_shutdown_disposes_the_engine(
        self, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disposed: list[bool] = []

        async def fake_dispose() -> None:
            disposed.append(True)

        monkeypatch.setattr(main, "dispose_engine", fake_dispose)

        async with app.router.lifespan_context(app):
            # Startup opens nothing: the engine is lazy, so a slow Postgres must never
            # stop the process from booting.
            assert disposed == []

        assert disposed == [True]


class TestSchemaOwnership:
    def test_the_app_never_creates_its_own_tables(self) -> None:
        """`CLAUDE.md` §4: schema comes from Alembic only, outside of tests.

        Parsed rather than grepped so that prose mentioning the rule (this module, and
        `app/main.py`'s own docstring) does not trip it — only a real call does.
        """
        offenders = []
        for path in Path(main.__file__).parent.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {"create_all", "drop_all"}:
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []
