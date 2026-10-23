"""A minimal HTTP server that returns the request's raw header lines."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress

DEFAULT_PORT = 8080
HEADER_TIMEOUT_SECONDS = 5.0
MAX_REQUEST_HEAD_SIZE = 16 * 1024

_REQUEST_LINE = re.compile(rb"[^\x00-\x20\x7f]+ [^\x00-\x20\x7f]+ HTTP/1\.[01]")
_ERROR_BODIES = {
    400: b"Bad Request\n",
    408: b"Request Timeout\n",
    431: b"Request Header Fields Too Large\n",
}


def _cli_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("PORT must be an integer") from error

    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("PORT must be between 1 and 65535")
    return port


def resolve_port(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve and validate the listening port from CLI arguments and the environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", metavar="PORT", type=_cli_port, help="TCP port to listen on")
    arguments = parser.parse_args(argv)
    if arguments.port is not None:
        return arguments.port

    port_text = (os.environ if environ is None else environ).get("PORT", str(DEFAULT_PORT))
    try:
        port = int(port_text)
    except ValueError as error:
        raise SystemExit("PORT must be an integer") from error

    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")
    return port


class IncompleteRequestError(Exception):
    """Raised when a complete HTTP request head was not received."""


class RequestHeadTooLargeError(Exception):
    """Raised when an HTTP request head exceeds its configured limit."""


async def _read_request_head(
    reader: asyncio.StreamReader,
    max_size: int,
) -> bytes:
    data = bytearray()

    while True:
        boundary = data.find(b"\r\n\r\n")
        if boundary >= 0:
            end = boundary + 4
            if end > max_size:
                raise RequestHeadTooLargeError
            return bytes(data[:end])

        if len(data) >= max_size:
            raise RequestHeadTooLargeError

        chunk = await reader.read(min(4096, max_size - len(data)))
        if not chunk:
            raise IncompleteRequestError
        data.extend(chunk)


def _raw_header_lines(request_head: bytes) -> bytes:
    request_line_end = request_head.find(b"\r\n")
    if request_line_end < 0:
        raise IncompleteRequestError

    request_line = request_head[:request_line_end]
    if _REQUEST_LINE.fullmatch(request_line) is None:
        raise IncompleteRequestError

    boundary = len(request_head) - 4
    # Keep the CRLF ending the final header line, but omit the empty line after it.
    return request_head[request_line_end + 2 : boundary + 2]


def _response(status: int, body: bytes) -> bytes:
    reasons = {
        200: b"OK",
        400: b"Bad Request",
        408: b"Request Timeout",
        431: b"Request Header Fields Too Large",
    }
    return b"".join(
        (
            b"HTTP/1.1 ",
            str(status).encode("ascii"),
            b" ",
            reasons[status],
            b"\r\nContent-Type: text/plain\r\nContent-Length: ",
            str(len(body)).encode("ascii"),
            b"\r\nConnection: close\r\n\r\n",
            body,
        )
    )


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    max_header_size: int = MAX_REQUEST_HEAD_SIZE,
    timeout: float = HEADER_TIMEOUT_SECONDS,
) -> None:
    """Handle one HTTP request and close the connection."""
    try:
        try:
            request_head = await asyncio.wait_for(
                _read_request_head(reader, max_header_size),
                timeout=timeout,
            )
            body = _raw_header_lines(request_head)
            response = _response(200, body)
        except asyncio.TimeoutError:
            response = _response(408, _ERROR_BODIES[408])
        except RequestHeadTooLargeError:
            response = _response(431, _ERROR_BODIES[431])
        except IncompleteRequestError:
            response = _response(400, _ERROR_BODIES[400])

        writer.write(response)
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await writer.wait_closed()


async def serve(port: int) -> None:
    """Run the raw-header HTTP server until it is stopped."""
    server = await asyncio.start_server(handle_client, "0.0.0.0", port)
    print(f"Listening on 0.0.0.0:{port}", flush=True)
    async with server:
        await server.serve_forever()


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Run the server, exiting cleanly when interrupted by the user."""
    port = resolve_port(argv, environ)
    try:
        asyncio.run(serve(port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
