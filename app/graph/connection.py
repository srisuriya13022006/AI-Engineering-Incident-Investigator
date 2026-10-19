import logging
from typing import Optional, Tuple
from falkordb import FalkorDB
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger("mai.graph.connection")


class FalkorDBConnectionError(Exception):
    """Raised when communication with the FalkorDB instance fails."""
    pass


def get_falkordb_client(
    host: Optional[str] = None,
    port: Optional[int] = None,
    password: Optional[str] = None,
    timeout_sec: Optional[float] = None,
    username: Optional[str] = None,
    ssl: Optional[bool] = None,
) -> FalkorDB:
    """Instantiate a FalkorDB client with configured connection parameters."""
    target_host = host or settings.falkordb_host
    target_port = port or settings.falkordb_port
    target_username = username if username is not None else settings.falkordb_username
    target_password = password if password is not None else settings.falkordb_password
    target_ssl = ssl if ssl is not None else settings.falkordb_ssl
    target_timeout = timeout_sec or settings.falkordb_timeout_sec

    # Normalize empty or whitespace-only strings to None
    if isinstance(target_username, str) and not target_username.strip():
        target_username = None
    if isinstance(target_password, str) and not target_password.strip():
        target_password = None

    try:
        client = FalkorDB(
            host=target_host,
            port=target_port,
            username=target_username,
            password=target_password,
            ssl=target_ssl,
            socket_connect_timeout=target_timeout,
            socket_timeout=target_timeout,
        )
        return client
    except (RedisConnectionError, RedisTimeoutError, RedisError, OSError) as exc:
        logger.error(
            f"Failed to connect to FalkorDB at {target_host}:{target_port} (ssl={target_ssl}) - {exc}"
        )
        raise FalkorDBConnectionError(
            f"Cannot connect to FalkorDB at {target_host}:{target_port}: {exc}"
        ) from exc


def get_graph_client(
    graph_name: Optional[str] = None,
    client: Optional[FalkorDB] = None,
):
    """Select and return a FalkorDB graph instance."""
    db_client = client or get_falkordb_client()
    target_graph = graph_name or settings.falkordb_graph
    try:
        return db_client.select_graph(target_graph)
    except (RedisConnectionError, RedisTimeoutError, RedisError, OSError) as exc:
        raise FalkorDBConnectionError(f"Failed to select graph '{target_graph}': {exc}") from exc


def check_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    ssl: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """Test connectivity to the FalkorDB instance."""
    target_host = host or settings.falkordb_host
    target_port = port or settings.falkordb_port
    try:
        client = get_falkordb_client(
            host=target_host,
            port=target_port,
            username=username,
            password=password,
            ssl=ssl,
        )
        # Execute quick probe to verify engine responsiveness
        client.connection.ping()
        return True, None
    except Exception as exc:
        return False, str(exc)
