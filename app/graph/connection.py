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
) -> FalkorDB:
    """Instantiate a FalkorDB client with configured connection parameters."""
    target_host = host or settings.falkordb_host
    target_port = port or settings.falkordb_port
    target_password = password if password is not None else settings.falkordb_password
    target_timeout = timeout_sec or settings.falkordb_timeout_sec

    try:
        client = FalkorDB(
            host=target_host,
            port=target_port,
            password=target_password,
            socket_connect_timeout=target_timeout,
            socket_timeout=target_timeout,
        )
        return client
    except (RedisConnectionError, RedisTimeoutError, RedisError, OSError) as exc:
        logger.error(f"Failed to connect to FalkorDB at {target_host}:{target_port} - {exc}")
        raise FalkorDBConnectionError(f"Cannot connect to FalkorDB at {target_host}:{target_port}: {exc}") from exc


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
) -> Tuple[bool, Optional[str]]:
    """Test connectivity to the FalkorDB instance."""
    target_host = host or settings.falkordb_host
    target_port = port or settings.falkordb_port
    try:
        client = get_falkordb_client(host=target_host, port=target_port)
        # Execute quick probe to verify engine responsiveness
        client.connection.ping()
        return True, None
    except Exception as exc:
        return False, str(exc)
