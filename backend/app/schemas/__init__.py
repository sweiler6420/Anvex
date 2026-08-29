"""Pydantic request/response contracts — the API's public shape (``CLAUDE.md`` §3).

One module per resource, plus the framework-level pieces every endpoint shares: the error
envelope, the health bodies, and the ``Page[T]`` list envelope.

Three rules hold across all of them:

* **An ORM model never crosses this boundary.** A handler returns an ``XOut``, never a
  ``User``; a body is an ``XCreate``, never a ``models.Stock``. Changing one of these
  classes is a breaking API change and is treated as one.
* **Every ``XOut`` sets** ``model_config = ConfigDict(from_attributes=True)``, because the
  thing it is built from is a model instance.
* **Optionality mirrors the database exactly.** The only nullable columns in Anvex are
  ``stocks.isin`` and ``politicians.state``/``chamber``/``dob``/``gender``; no other output
  field is ``| None``. A defensive ``| None`` on a ``NOT NULL`` column pushes a null check
  onto every client forever, to guard against a state that cannot happen.
"""

from app.schemas.auth import (
    TOKEN_TYPE,
    RecoveryRequest,
    RefreshRequest,
    TokenPair,
    TokenPayload,
    TokenType,
)
from app.schemas.errors import ErrorBody, ErrorResponse
from app.schemas.health import HealthOut, ReadinessOut
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.politician import PoliticianCreate, PoliticianOut
from app.schemas.stock import StockCreate, StockOut, StockUpdate
from app.schemas.stock_data import StockDataCreate, StockDataOut, StockDataPoint
from app.schemas.user import PasswordChange, UserCreate, UserOut, UserUpdate
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistDetailOut,
    WatchlistEntryCreate,
    WatchlistEntryDetailOut,
    WatchlistEntryOut,
    WatchlistEntryUpdate,
    WatchlistOut,
    WatchlistUpdate,
)

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "TOKEN_TYPE",
    "ErrorBody",
    "ErrorResponse",
    "HealthOut",
    "Page",
    "PasswordChange",
    "PoliticianCreate",
    "PoliticianOut",
    "ReadinessOut",
    "RecoveryRequest",
    "RefreshRequest",
    "StockCreate",
    "StockDataCreate",
    "StockDataOut",
    "StockDataPoint",
    "StockOut",
    "StockUpdate",
    "TokenPair",
    "TokenPayload",
    "TokenType",
    "UserCreate",
    "UserOut",
    "UserUpdate",
    "WatchlistCreate",
    "WatchlistDetailOut",
    "WatchlistEntryCreate",
    "WatchlistEntryDetailOut",
    "WatchlistEntryOut",
    "WatchlistEntryUpdate",
    "WatchlistOut",
    "WatchlistUpdate",
]
