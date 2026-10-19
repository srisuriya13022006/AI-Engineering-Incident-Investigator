"""
Unit tests for FalkorDB connection configuration and authentication.
Verifies support for FALKORDB_USERNAME and FALKORDB_SSL for FalkorDB Cloud
while preserving default local Docker behavior and ensuring credentials are never logged.
"""

import logging
from unittest.mock import MagicMock, patch
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.graph.connection import (
    get_falkordb_client,
    get_graph_client,
    check_connection,
    FalkorDBConnectionError,
)


def test_default_connection_settings():
    """Verify default settings preserve local Docker defaults without requiring SSL or username."""
    default_settings = Settings(
        _env_file=None,  # ignore local .env
    )
    assert default_settings.falkordb_host == "127.0.0.1"
    assert default_settings.falkordb_port == 6379
    assert default_settings.falkordb_graph == "mai_agent_memory"
    assert default_settings.falkordb_username is None
    assert default_settings.falkordb_password is None
    assert default_settings.falkordb_ssl is False
    assert default_settings.falkordb_timeout_sec == 2.0


def test_settings_env_parsing(monkeypatch):
    """Verify FALKORDB_USERNAME and FALKORDB_SSL environment variables are correctly parsed."""
    monkeypatch.setenv("FALKORDB_HOST", "falkordb-cloud.example.com")
    monkeypatch.setenv("FALKORDB_PORT", "12345")
    monkeypatch.setenv("FALKORDB_GRAPH", "production_graph")
    monkeypatch.setenv("FALKORDB_USERNAME", "default")
    monkeypatch.setenv("FALKORDB_PASSWORD", "super_secret_token_123")
    monkeypatch.setenv("FALKORDB_SSL", "true")

    cloud_settings = Settings(_env_file=None)
    assert cloud_settings.falkordb_host == "falkordb-cloud.example.com"
    assert cloud_settings.falkordb_port == 12345
    assert cloud_settings.falkordb_graph == "production_graph"
    assert cloud_settings.falkordb_username == "default"
    assert cloud_settings.falkordb_password == "super_secret_token_123"
    assert cloud_settings.falkordb_ssl is True


@pytest.mark.parametrize(
    "ssl_val,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
    ],
)
def test_settings_ssl_boolean_coercion(monkeypatch, ssl_val, expected):
    """Verify various string values for FALKORDB_SSL are parsed into booleans."""
    monkeypatch.setenv("FALKORDB_SSL", ssl_val)
    s = Settings(_env_file=None)
    assert s.falkordb_ssl is expected


def test_settings_normalizes_empty_strings(monkeypatch):
    """Verify empty string values for username and password become None."""
    monkeypatch.setenv("FALKORDB_USERNAME", "   ")
    monkeypatch.setenv("FALKORDB_PASSWORD", "")
    s = Settings(_env_file=None)
    assert s.falkordb_username is None
    assert s.falkordb_password is None


@patch("app.graph.connection.FalkorDB")
def test_get_falkordb_client_passes_cloud_auth_arguments(mock_falkordb_cls):
    """Verify username, password, and ssl settings are passed to the official FalkorDB client."""
    mock_instance = MagicMock()
    mock_falkordb_cls.return_value = mock_instance

    client = get_falkordb_client(
        host="falkor-db.c1.falkordb.cloud",
        port=19999,
        username="default",
        password="cloud_password_xyz",
        ssl=True,
        timeout_sec=5.0,
    )

    assert client == mock_instance
    mock_falkordb_cls.assert_called_once_with(
        host="falkor-db.c1.falkordb.cloud",
        port=19999,
        username="default",
        password="cloud_password_xyz",
        ssl=True,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
    )


@patch("app.graph.connection.FalkorDB")
def test_get_falkordb_client_uses_settings_by_default(mock_falkordb_cls):
    """Verify get_falkordb_client reads from global settings if arguments are omitted."""
    from app.config import settings

    with patch.object(settings, "falkordb_host", "cloud-host.net"), \
         patch.object(settings, "falkordb_port", 6380), \
         patch.object(settings, "falkordb_username", "render_app"), \
         patch.object(settings, "falkordb_password", "render_pass"), \
         patch.object(settings, "falkordb_ssl", True), \
         patch.object(settings, "falkordb_timeout_sec", 3.0):

        get_falkordb_client()

        mock_falkordb_cls.assert_called_once_with(
            host="cloud-host.net",
            port=6380,
            username="render_app",
            password="render_pass",
            ssl=True,
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
        )


@patch("app.graph.connection.FalkorDB")
def test_get_falkordb_client_normalizes_empty_credentials(mock_falkordb_cls):
    """Verify empty string username/password arguments are converted to None."""
    get_falkordb_client(
        host="localhost",
        port=6379,
        username="   ",
        password="",
        ssl=False,
    )

    mock_falkordb_cls.assert_called_once_with(
        host="localhost",
        port=6379,
        username=None,
        password=None,
        ssl=False,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )


@patch("app.graph.connection.FalkorDB")
def test_get_falkordb_client_never_logs_credentials(mock_falkordb_cls, caplog):
    """Verify connection errors log host and ssl flag, but NEVER expose password."""
    mock_falkordb_cls.side_effect = RedisConnectionError("invalid username-password pair or user is disabled")
    secret_pass = "ultra_confidential_pw_999"

    with caplog.at_level(logging.ERROR, logger="mai.graph.connection"):
        with pytest.raises(FalkorDBConnectionError) as exc_info:
            get_falkordb_client(
                host="cloud.falkordb.net",
                port=12345,
                username="default",
                password=secret_pass,
                ssl=True,
            )

    # Check error message and logs do not contain the password
    assert secret_pass not in str(exc_info.value)
    assert secret_pass not in caplog.text
    assert "cloud.falkordb.net:12345" in caplog.text
    assert "ssl=True" in caplog.text


@patch("app.graph.connection.get_falkordb_client")
def test_check_connection_forwards_auth_parameters(mock_get_client):
    """Verify check_connection passes username, password, and ssl to get_falkordb_client."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.connection.ping.return_value = True

    is_ok, err = check_connection(
        host="render-cloud.falkordb.net",
        port=1111,
        username="my_user",
        password="my_password",
        ssl=True,
    )

    assert is_ok is True
    assert err is None
    mock_get_client.assert_called_once_with(
        host="render-cloud.falkordb.net",
        port=1111,
        username="my_user",
        password="my_password",
        ssl=True,
    )
    mock_client.connection.ping.assert_called_once()


@patch("app.graph.connection.get_falkordb_client")
def test_check_connection_returns_clean_error_on_auth_failure(mock_get_client):
    """Verify check_connection catches authentication failures and returns error message cleanly."""
    mock_get_client.side_effect = FalkorDBConnectionError("invalid username-password pair or user is disabled")

    is_ok, err = check_connection(
        host="cloud.falkordb.net",
        port=12345,
        username="bad_user",
        password="bad_password",
        ssl=True,
    )

    assert is_ok is False
    assert "invalid username-password pair" in err


@patch("app.graph.connection.get_falkordb_client")
def test_get_graph_client_delegates_to_falkordb_client(mock_get_client):
    """Verify get_graph_client delegates to get_falkordb_client with graph selection."""
    mock_client = MagicMock()
    mock_graph = MagicMock()
    mock_client.select_graph.return_value = mock_graph
    mock_get_client.return_value = mock_client

    graph = get_graph_client(graph_name="test_graph")
    assert graph == mock_graph
    mock_client.select_graph.assert_called_once_with("test_graph")
