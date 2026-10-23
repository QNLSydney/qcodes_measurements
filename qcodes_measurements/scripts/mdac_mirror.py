"""Forward ZeroMQ requests through the bundled MDAC driver's serial transport.

The client owns MDAC protocol negotiation, checksums, sequence numbers and reply
continuations. Forward the wire format unchanged rather than parsing it through
another MDAC instrument instance.
"""

import argparse
import logging
from collections.abc import Sequence
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from typing import cast

import zmq

from qcodes_measurements.drivers.MDAC import MDAC, calculate_checksum, serial_MDAC
from qcodes_measurements.scripts._arguments import port_number, positive_seconds

logger = logging.getLogger(__name__)


def _error_reply(command: str) -> bytes:
    """Frame a local error using the request's checksum and sequence, if present."""
    wire_command = command.lstrip("~")
    has_checksum = (
        len(wire_command) >= 4
        and wire_command[-4:].upper() == f"{calculate_checksum(wire_command[:-4]):04X}"
    )
    if has_checksum:
        wire_command = wire_command[:-4]
    sequence, separator, _ = wire_command.partition("|")
    prefix = f"{sequence}|" if separator and sequence.isdecimal() else ""
    reply = f"{prefix}[ERR:0x0000]"
    if has_checksum:
        reply += f"{calculate_checksum(reply):04X}"
    return reply.encode("utf-8")


def _split_client_message(message: Sequence[bytes]) -> tuple[bytes, bool, list[bytes]]:
    """Split ROUTER envelopes for both REQ and DEALER clients."""
    if not message:
        raise ValueError("Received empty message from client")
    has_delimiter = len(message) > 1 and message[1] == b""
    return message[0], has_delimiter, list(message[2:] if has_delimiter else message[1:])


@dataclass(frozen=True)
class MirrorConfig:
    """Serial connection and ZeroMQ listener settings."""

    serial_port: str
    listen_host: str = "127.0.0.1"
    listen_port: int = 5000
    request_timeout: float = 1.0

    @property
    def listen_endpoint(self) -> str:
        """Return the ZeroMQ bind endpoint."""
        host = self.listen_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"tcp://{host}:{self.listen_port}"


class ZmqInstrumentMirror:
    """Serialize client transactions over a single serial connection."""

    log = logger  # serial_MDAC uses its parent only for logging.
    _POLL_TIMEOUT_MS = 500

    def __init__(self, config: MirrorConfig) -> None:
        self.config = config
        self._running = False
        self._resources = ExitStack()
        try:
            self.context = zmq.Context()
            self._resources.callback(self.context.term)
            self.client_socket = self.context.socket(zmq.ROUTER)
            self._resources.callback(self.client_socket.close, linger=0)
            self.client_socket.setsockopt(zmq.LINGER, 0)
            self.client_socket.setsockopt(zmq.ROUTER_MANDATORY, 1)
            self.client_socket.setsockopt(zmq.IPV6, 1)
            # The transport's parent annotation names MDAC, but it needs only .log.
            self.instrument = serial_MDAC(cast(MDAC, self), config.serial_port)
            self._resources.callback(self.instrument.close)
            self.poller = zmq.Poller()
        except BaseException:
            self._resources.close()
            raise

    def serve_forever(self) -> None:
        """Serve requests until interrupted or stopped."""
        self.client_socket.bind(self.config.listen_endpoint)
        self.poller.register(self.client_socket, zmq.POLLIN)
        self._running = True
        logger.info(
            "Listening on %s; serial port %s", self.config.listen_endpoint, self.config.serial_port
        )
        while self._running:
            try:
                events = dict(self.poller.poll(self._POLL_TIMEOUT_MS))
            except zmq.ContextTerminated:
                break
            if events.get(self.client_socket, 0) & zmq.POLLIN:
                with suppress(zmq.Again):
                    self._handle_client_message(self.client_socket.recv_multipart(zmq.NOBLOCK))

    def stop(self) -> None:
        """Request shutdown on the next poll iteration."""
        self._running = False

    def _handle_client_message(self, message: Sequence[bytes]) -> None:
        identity, had_delimiter, payload = _split_client_message(message)
        command = ""
        try:
            if len(payload) != 1:
                raise ValueError("Expected a single command frame")
            command = payload[0].decode("utf-8").rstrip("\r\n")
            if "\r" in command or "\n" in command:
                raise ValueError("Expected a single MDAC command")
            logger.debug("Client %s -> MDAC: %r", identity.hex(), command)
            reply = b""
            if command:
                if not self.instrument.write(command):
                    raise OSError("Incomplete serial write")
                reply = self.instrument.read(
                    timeout_Secs=self.config.request_timeout, failure_is_fatal=False
                ).encode("utf-8")
                if not reply:
                    # REQ clients require a reply even when the serial command is
                    # silent. An empty reply makes MDAC clients wait for more data.
                    logger.warning("No MDAC reply within %ss", self.config.request_timeout)
                    reply = _error_reply(command)
        except Exception:  # pylint: disable=broad-exception-caught
            # Keep the ROUTER service alive after a single failed client request.
            logger.exception("MDAC request failed for client %s", identity.hex())
            # Preserve the MDAC error marker's case so clients recognize a failure.
            reply = _error_reply(command)

        frames = [identity, b"", reply] if had_delimiter else [identity, reply]
        try:
            self.client_socket.send_multipart(frames, flags=zmq.NOBLOCK)
        except zmq.Again:
            logger.warning("Client %s is not receiving replies", identity.hex())
        except zmq.ZMQError as exc:
            if exc.errno != zmq.EHOSTUNREACH:
                raise
            logger.warning("Client %s disconnected", identity.hex())

    def close(self) -> None:
        """Release serial and ZeroMQ resources, including after startup failure."""
        self.stop()
        self._resources.close()


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line without opening hardware or configuring logging."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("serial_port", help="Serial port, e.g. COM4 or /dev/ttyUSB0")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument(
        "--listen-port", type=port_number, default=5000, help="Bind port (default: 5000)"
    )
    parser.add_argument(
        "--request-timeout",
        type=positive_seconds,
        default=1.0,
        help="Serial response timeout in seconds (default: 1)",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"
    )
    return parser.parse_args(argv)


def main() -> None:
    """Run the installed mdac-mirror command."""
    args = parse_arguments()
    logging.basicConfig(level=args.log_level, format="%(asctime)s - %(levelname)s - %(message)s")
    config = MirrorConfig(
        args.serial_port, args.listen_host, args.listen_port, args.request_timeout
    )
    mirror = ZmqInstrumentMirror(config)
    try:
        mirror.serve_forever()
    except KeyboardInterrupt:
        logger.info("Mirror shutdown by user")
    finally:
        mirror.close()


if __name__ == "__main__":
    main()
