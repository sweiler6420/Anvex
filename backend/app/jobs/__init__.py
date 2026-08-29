"""Celery tasks and their wiring.

``app/jobs/celery_app.py`` owns the application, the execution policy and the beat schedule;
``app/jobs/base.py`` owns :func:`~app.jobs.base.run_async`, the **one** bridge from Celery's
synchronous world into Anvex's async one; every other module here holds tasks.

A task is a thin entry point that resolves its dependencies and calls one service
(``CLAUDE.md`` §3). Adding one means: write the async half, wrap it in a task whose body is a
single ``run_async(...)``, give it an explicit ``name=``, and list its module in
``celery_app.TASK_MODULES``.

**The ``celery_app`` object is deliberately not re-exported here.** Binding that name in the
package would shadow the *module* of the same name — ``app.jobs.celery_app`` would resolve to
a :class:`~celery.Celery` instance rather than to the module holding it, and anything reaching
for a constant or a signal handler in that module (a test, a script, ``celery -A``) would get
a confusing ``AttributeError`` instead. Import it from where it lives:
``from app.jobs.celery_app import celery_app``.
"""

from __future__ import annotations

from app.jobs.base import AnvexTask, run_async
from app.jobs.celery_app import create_celery_app

__all__ = ["AnvexTask", "create_celery_app", "run_async"]
