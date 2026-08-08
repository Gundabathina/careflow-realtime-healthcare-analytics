"""PostgreSQL connection management for the CareFlow warehouse.

Credentials come only from environment variables (see .env.example) --
never hard-coded, never logged. Connection errors are sanitized before
they reach logs or reports. ``validate_identifier`` is the only
sanctioned way a dynamically-chosen schema/table name may be interpolated
into SQL text elsewhere in this package: it enforces a strict character
pattern and, when given an allow-list, membership in it.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlsplit

import psycopg

from careflow.logging_config import get_logger

logger = get_logger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

REQUIRED_ENV_VARS: tuple[str, ...] = (
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
)


class MissingCredentialsError(Exception):
    """Raised when a required PostgreSQL environment variable is not set."""


class UnsafeIdentifierError(Exception):
    """Raised when a schema/table identifier fails validation against the allow-list."""


class WarehouseConnectionError(Exception):
    """Raised when a PostgreSQL connection cannot be established. Message is sanitized."""


@dataclass(frozen=True)
class PostgresConnectionConfig:
    """Resolved PostgreSQL connection parameters."""

    host: str
    port: int
    dbname: str
    user: str
    password: str
    connect_timeout: int = 10
    sslmode: str = "prefer"

    def as_conninfo_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
            "sslmode": self.sslmode,
        }

    def safe_repr(self) -> str:
        """A representation safe to log or include in reports -- never the password."""
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.dbname}"


def _parse_database_url(url: str, sslmode: str) -> PostgresConnectionConfig:
    """Parse a single bundled connection URL (e.g. Render's "External Database URL",
    ``postgresql://user:password@host:port/dbname``) into the same config shape as
    the five separate POSTGRES_* variables.

    Never includes the raw URL (which embeds the password) in any error
    message -- only field names, exactly like the POSTGRES_* path.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise MissingCredentialsError("DATABASE_URL is not a valid URL") from None

    missing = [
        name for name, value in (
            ("host", parts.hostname), ("user", parts.username),
            ("password", parts.password), ("dbname", parts.path.lstrip("/")),
        ) if not value
    ]
    if missing:
        raise MissingCredentialsError(
            f"DATABASE_URL is missing required component(s): {', '.join(missing)}."
        )
    return PostgresConnectionConfig(
        host=parts.hostname,
        port=parts.port or 5432,
        dbname=parts.path.lstrip("/"),
        user=parts.username,
        password=parts.password,
        sslmode=sslmode,
    )


def load_connection_config(env: dict | None = None) -> PostgresConnectionConfig:
    """Build connection config strictly from environment variables.

    Supports two forms, checked in this order:

    1. ``DATABASE_URL`` -- a single bundled connection string (e.g. what
       Render's "Connect" page shows as the "External Database URL").
    2. The five separate ``POSTGRES_HOST``/``POSTGRES_PORT``/``POSTGRES_DB``/
       ``POSTGRES_USER``/``POSTGRES_PASSWORD`` variables (unchanged,
       original behavior).

    ``POSTGRES_SSLMODE`` applies to either form. Raises
    :class:`MissingCredentialsError` naming which variable(s)/component(s)
    are missing -- the error message never includes any credential value.
    """
    source = env if env is not None else os.environ
    sslmode = source.get("POSTGRES_SSLMODE") or "prefer"

    database_url = source.get("DATABASE_URL")
    if database_url:
        return _parse_database_url(database_url, sslmode)

    missing = [name for name in REQUIRED_ENV_VARS if not source.get(name)]
    if missing:
        raise MissingCredentialsError(
            f"Missing required PostgreSQL environment variable(s): {', '.join(missing)}. "
            "See .env.example."
        )
    try:
        port = int(source["POSTGRES_PORT"])
    except ValueError:
        raise MissingCredentialsError("POSTGRES_PORT must be an integer") from None
    return PostgresConnectionConfig(
        host=source["POSTGRES_HOST"],
        port=port,
        dbname=source["POSTGRES_DB"],
        user=source["POSTGRES_USER"],
        password=source["POSTGRES_PASSWORD"],
        sslmode=sslmode,
    )


def validate_identifier(name: str, allowed: set[str] | None = None) -> str:
    """Validate a schema/table identifier before it is interpolated into SQL text.

    Only ``[a-zA-Z_][a-zA-Z0-9_]*`` identifiers are accepted; when
    ``allowed`` is given, ``name`` must also be a member of it. This is
    the sole path by which a dynamic identifier reaches SQL in this
    package -- it never comes directly from unrestricted user input.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise UnsafeIdentifierError(f"Unsafe SQL identifier: {name!r}")
    if allowed is not None and name not in allowed:
        raise UnsafeIdentifierError(f"Identifier not in the allowed registry: {name!r}")
    return name


def _sanitize_error(exc: BaseException) -> str:
    """Strip anything resembling a password/DSN fragment from a driver error message."""
    text = str(exc)
    text = re.sub(r"password=\S+", "password=***", text, flags=re.IGNORECASE)
    text = re.sub(r"://[^:@/]+:[^@/]+@", "://***:***@", text)
    return text


@contextmanager
def get_connection(config: PostgresConnectionConfig | None = None) -> Iterator[psycopg.Connection]:
    """Yield a psycopg connection with autocommit disabled (caller manages transactions).

    Raises :class:`WarehouseConnectionError` with a sanitized message on
    failure -- never the raw driver exception, which can embed the DSN.
    """
    cfg = config or load_connection_config()
    try:
        conn = psycopg.connect(**cfg.as_conninfo_kwargs())
    except psycopg.OperationalError as exc:
        raise WarehouseConnectionError(
            f"Could not connect to PostgreSQL at {cfg.safe_repr()}: {_sanitize_error(exc)}"
        ) from None
    try:
        yield conn
    finally:
        conn.close()


def check_connectivity(config: PostgresConnectionConfig | None = None) -> tuple[bool, str | None]:
    """Return (True, None) if PostgreSQL is reachable, else (False, sanitized_reason)."""
    try:
        with get_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, None
    except WarehouseConnectionError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, _sanitize_error(exc)
