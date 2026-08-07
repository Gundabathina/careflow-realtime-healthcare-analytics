"""Tests for careflow.warehouse.postgres_client.

All tests use mocks; none require a running PostgreSQL server.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from careflow.warehouse import postgres_client as pc


# -- connection configuration -----------------------------------------------------


def test_load_connection_config_from_env():
    env = {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_DB": "careflow",
        "POSTGRES_USER": "careflow_user", "POSTGRES_PASSWORD": "secret123",
    }
    cfg = pc.load_connection_config(env)
    assert cfg.host == "localhost"
    assert cfg.port == 5432
    assert cfg.dbname == "careflow"
    assert cfg.user == "careflow_user"
    assert cfg.password == "secret123"


def test_missing_env_vars_names_vars_without_leaking_values():
    env = {"POSTGRES_HOST": "localhost", "POSTGRES_PASSWORD": "secret123"}
    with pytest.raises(pc.MissingCredentialsError) as exc_info:
        pc.load_connection_config(env)
    message = str(exc_info.value)
    assert "POSTGRES_PORT" in message
    assert "POSTGRES_DB" in message
    assert "POSTGRES_USER" in message
    assert "secret123" not in message


def test_missing_all_env_vars_raises():
    with pytest.raises(pc.MissingCredentialsError):
        pc.load_connection_config({})


def test_invalid_port_raises_missing_credentials_error():
    env = {
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "not-a-number", "POSTGRES_DB": "careflow",
        "POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p",
    }
    with pytest.raises(pc.MissingCredentialsError):
        pc.load_connection_config(env)


# -- password never logged ---------------------------------------------------------


def test_safe_repr_never_contains_password():
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="super-secret")
    assert "super-secret" not in cfg.safe_repr()
    assert "***" in cfg.safe_repr()


def test_connection_error_message_never_contains_password(monkeypatch):
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="super-secret-pw")

    def fake_connect(**kwargs):
        raise pc.psycopg.OperationalError("connection failed password=super-secret-pw")

    monkeypatch.setattr(pc.psycopg, "connect", fake_connect)
    with pytest.raises(pc.WarehouseConnectionError) as exc_info:
        with pc.get_connection(cfg):
            pass
    assert "super-secret-pw" not in str(exc_info.value)


def test_sanitize_error_masks_dsn_credentials():
    sanitized = pc._sanitize_error(Exception("postgresql://careflow_user:hunter2@localhost:5432/db"))
    assert "hunter2" not in sanitized
    assert "***" in sanitized


# -- safe identifier validation -----------------------------------------------------


def test_validate_identifier_accepts_safe_name():
    assert pc.validate_identifier("dim_patient") == "dim_patient"


def test_validate_identifier_rejects_sql_injection_attempt():
    with pytest.raises(pc.UnsafeIdentifierError):
        pc.validate_identifier("dim_patient; DROP TABLE foo;--")


def test_validate_identifier_rejects_whitespace_and_quotes():
    with pytest.raises(pc.UnsafeIdentifierError):
        pc.validate_identifier('dim_patient" OR "1"="1')


def test_validate_identifier_rejects_non_allowlisted_name():
    with pytest.raises(pc.UnsafeIdentifierError):
        pc.validate_identifier("dim_patient", allowed={"dim_encounter"})


def test_validate_identifier_accepts_allowlisted_name():
    assert pc.validate_identifier("dim_patient", allowed={"dim_patient", "dim_encounter"}) == "dim_patient"


def test_validate_identifier_rejects_non_string():
    with pytest.raises(pc.UnsafeIdentifierError):
        pc.validate_identifier(123)  # type: ignore[arg-type]


# -- connection handling -------------------------------------------------------------


def test_check_connectivity_success(monkeypatch):
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchone = MagicMock(return_value=(1,))

    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cursor)

    monkeypatch.setattr(pc.psycopg, "connect", lambda **kwargs: fake_conn)
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="p")
    ok, reason = pc.check_connectivity(cfg)
    assert ok is True
    assert reason is None


def test_check_connectivity_failure_returns_sanitized_reason(monkeypatch):
    def fake_connect(**kwargs):
        raise pc.psycopg.OperationalError("could not connect password=abc123")

    monkeypatch.setattr(pc.psycopg, "connect", fake_connect)
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="p")
    ok, reason = pc.check_connectivity(cfg)
    assert ok is False
    assert reason is not None
    assert "abc123" not in reason


def test_get_connection_closes_connection_on_exit(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(pc.psycopg, "connect", lambda **kwargs: fake_conn)
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="p")
    with pc.get_connection(cfg) as conn:
        assert conn is fake_conn
    fake_conn.close.assert_called_once()


def test_get_connection_closes_even_on_exception(monkeypatch):
    fake_conn = MagicMock()
    monkeypatch.setattr(pc.psycopg, "connect", lambda **kwargs: fake_conn)
    cfg = pc.PostgresConnectionConfig(host="h", port=5432, dbname="d", user="u", password="p")
    with pytest.raises(ValueError):
        with pc.get_connection(cfg):
            raise ValueError("boom")
    fake_conn.close.assert_called_once()
