"""Share a persistent connection to a newline-delimited SCPI instrument."""

import argparse
import asyncio
import logging
import math
import socket
from collections.abc import Sequence
from contextlib import suppress

from qcodes_measurements.scripts._arguments import port_number, positive_seconds

logger = logging.getLogger(__name__)

EQUIPMENT_PORT = 7655
PROXY_PORT = 7655
RECONNECT_DELAY = 5
MAX_LINE_BYTES = 1024 * 1024


def _is_query(command: bytes) -> bool:
    """Find query markers outside SCPI's single- or double-quoted strings."""
    quote = None
    for char in command:
        if quote is not None:
            if char == quote:
                quote = None
        elif char in (ord('"'), ord("'")):
            quote = char
        elif char == ord("?"):
            return True
    return False


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with suppress(OSError):
        await writer.wait_closed()


class EquipmentProxy:
    """Serialize complete SCPI command/reply transactions across clients.

    Commands and text replies must end in LF (CRLF is also preserved). Binary
    blocks and unterminated commands are not supported by this line-based proxy.
    """

    def __init__(
        self,
        equipment_host: str,
        listen_addr: str | None,
        listen_port: int,
        keepalive_interval: float = 1.0,
        *,
        equipment_port: int = EQUIPMENT_PORT,
        request_timeout: float = 1.0,
    ) -> None:
        self.equipment_host = equipment_host
        self.equipment_port = equipment_port
        self.listen_addr = listen_addr
        self.listen_port = listen_port
        self.keepalive_interval = keepalive_interval
        self.request_timeout = request_timeout
        self.equipment_reader: asyncio.StreamReader | None = None
        self.equipment_writer: asyncio.StreamWriter | None = None
        self._reply_task: asyncio.Task[None] | None = None
        self._pending_reply: asyncio.Future[bytes] | None = None
        self._transaction_lock = asyncio.Lock()
        self._clients: set[asyncio.Task[None]] = set()

    async def connect_equipment(self) -> None:
        """Connect with retry; cancellation interrupts both connection and delay."""
        if self._reply_task is not None and self._reply_task.done():
            await self._disconnect_equipment()
        while self.equipment_writer is None:
            try:
                async with asyncio.timeout(self.request_timeout):
                    reader, writer = await asyncio.open_connection(
                        self.equipment_host, self.equipment_port, limit=MAX_LINE_BYTES
                    )
            except (OSError, TimeoutError) as exc:
                logger.warning("Connection failed: %s. Retrying in %ss", exc, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            self.equipment_reader, self.equipment_writer = reader, writer
            self._reply_task = asyncio.create_task(self._receive_replies(reader))
            logger.info("Connected to equipment at %s:%s", self.equipment_host, self.equipment_port)
            self._set_keepalive(writer)

    async def _receive_replies(self, reader: asyncio.StreamReader) -> None:
        """Keep reading so replies arriving after an idle timeout are discarded."""
        try:
            while True:
                response = await reader.readuntil(b"\n")
                pending = self._pending_reply
                if pending is not None and not pending.done():
                    pending.set_result(response)
                else:
                    logger.warning("Discarding unsolicited or late equipment reply: %r", response)
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            pending = self._pending_reply
            if pending is not None and not pending.done():
                pending.set_exception(ConnectionError(f"Equipment reply stream failed: {exc}"))

    def _set_keepalive(self, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # macOS names the idle setting TCP_KEEPALIVE; Linux uses TCP_KEEPIDLE.
            idle = getattr(socket, "TCP_KEEPIDLE", getattr(socket, "TCP_KEEPALIVE", None))
            seconds = math.ceil(self.keepalive_interval)
            if idle is not None:
                sock.setsockopt(socket.IPPROTO_TCP, idle, seconds)
            interval = getattr(socket, "TCP_KEEPINTVL", None)
            if interval is not None:
                sock.setsockopt(socket.IPPROTO_TCP, interval, seconds)
        except OSError as exc:
            logger.warning("Failed to set TCP keepalive: %s", exc)

    async def _disconnect_equipment(self) -> None:
        writer = self.equipment_writer
        self.equipment_reader = self.equipment_writer = None
        task, self._reply_task = self._reply_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            await _close_writer(writer)

    async def _transact(self, command: bytes) -> bytes | None:
        async with self._transaction_lock:
            await self.connect_equipment()
            assert self.equipment_reader is not None
            assert self.equipment_writer is not None
            pending = asyncio.get_running_loop().create_future() if _is_query(command) else None
            self._pending_reply = pending
            try:
                async with asyncio.timeout(self.request_timeout):
                    logger.debug("Client -> equipment: %r", command)
                    self.equipment_writer.write(command)
                    await self.equipment_writer.drain()
                if pending is not None:
                    try:
                        async with asyncio.timeout(self.request_timeout):
                            response = await pending
                        logger.debug("Equipment -> client: %r", response)
                        return response
                    except TimeoutError:
                        logger.info(
                            "No equipment reply within %ss; keeping connection open",
                            self.request_timeout,
                        )
                return None
            except BaseException:
                # Never retry a write: the equipment may already have executed it.
                await self._disconnect_equipment()
                raise
            finally:
                self._pending_reply = None
                if pending is not None and not pending.done():
                    pending.cancel()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Forward complete client lines while retaining exclusive reply ownership."""
        task = asyncio.current_task()
        assert task is not None
        self._clients.add(task)
        client_addr = writer.get_extra_info("peername")
        logger.info("Client connected from %s", client_addr)
        try:
            while True:
                try:
                    command = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        logger.warning(
                            "Client %s disconnected with an incomplete command", client_addr
                        )
                    break
                response = await self._transact(command)
                if response is not None:
                    writer.write(response)
                    await writer.drain()
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            logger.warning("Closing client %s after communication failure: %s", client_addr, exc)
        finally:
            try:
                await _close_writer(writer)
            finally:
                self._clients.discard(task)
            logger.info("Connection closed for %s", client_addr)

    async def run(self) -> None:
        """Run until cancelled, then close clients and the persistent connection."""
        try:
            await self.connect_equipment()
            server = await asyncio.start_server(
                self.handle_client, self.listen_addr, self.listen_port, limit=MAX_LINE_BYTES
            )
            logger.info("Proxy listening on %s:%s", self.listen_addr, self.listen_port)
            async with server:
                await server.serve_forever()
        finally:
            clients = list(self._clients)
            for task in clients:
                task.cancel()
            await asyncio.gather(*clients, return_exceptions=True)
            await self._disconnect_equipment()


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Validate settings without connecting or configuring logging."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("equipment_host", help="Equipment IP address or hostname")
    parser.add_argument(
        "--listen",
        "-l",
        default="127.0.0.1",
        help="Bind address or hostname; '*' binds all interfaces (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=port_number,
        default=PROXY_PORT,
        help=f"Listen port (default: {PROXY_PORT})",
    )
    parser.add_argument(
        "--equipment-port",
        type=port_number,
        default=EQUIPMENT_PORT,
        help=f"Equipment port (default: {EQUIPMENT_PORT})",
    )
    parser.add_argument(
        "--keepalive-interval",
        type=positive_seconds,
        default=1.0,
        help="TCP keepalive idle/probe interval in seconds, rounded up (default: 1)",
    )
    parser.add_argument(
        "--request-timeout",
        type=positive_seconds,
        default=1.0,
        help="Connect, write and reply timeout in seconds (default: 1)",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"
    )
    args = parser.parse_args(argv)
    if args.listen == "*":
        args.listen = None
    return args


def main() -> None:
    """Run the installed yoko-mirror command."""
    args = parse_arguments()
    logging.basicConfig(level=args.log_level, format="%(asctime)s - %(levelname)s - %(message)s")
    proxy = EquipmentProxy(
        args.equipment_host,
        args.listen,
        args.port,
        args.keepalive_interval,
        equipment_port=args.equipment_port,
        request_timeout=args.request_timeout,
    )
    try:
        asyncio.run(proxy.run())
    except KeyboardInterrupt:
        logger.info("Proxy shutdown by user")


if __name__ == "__main__":
    main()
