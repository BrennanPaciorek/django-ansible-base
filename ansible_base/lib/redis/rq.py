from typing import Optional

from redis import Redis
from rq import Worker


class DABWorker(Worker):
    def _set_connection(self, connection: Optional['Redis']) -> 'Redis':
        try:
            current_socket_timeout = connection.connection_pool.connection_kwargs.get("socket_timeout")
            if current_socket_timeout is None:
                timeout_config = {"socket_timeout": self.connection_timeout}
                connection.connection_pool.connection_kwargs.update(timeout_config)
            return connection
        # If using RedisCluster, parse connection pool for all nodes.
        except AttributeError:
            nodes = connection.get_nodes()
            for node in nodes:
                current_socket_timeout = node.redis_connection.connection_pool.connection_kwargs.get("socket_timeout")
                if current_socket_timeout is None:
                    timeout_config = {"socket_timeout": self.connection_timeout}
                    node.redis_connection.connection_pool.connection_kwargs.update(timeout_config)
            return connection
