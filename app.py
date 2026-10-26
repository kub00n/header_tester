"""A minimal HTTP server that returns the request's raw header lines."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import ssl
from collections.abc import Mapping, Sequence
from contextlib import suppress

DEFAULT_PORT = 8080
HEADER_TIMEOUT_SECONDS = 5.0
TLS_TIMEOUT_SECONDS = 5.0
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


def resolve_configuration(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, str | None, str | None]:
    """Resolve CLI options with per-option environment fallback and validate TLS pairing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", metavar="PORT", type=_cli_port, help="TCP port to listen on")
    parser.add_argument("--tls-cert", metavar="FILE", help="PEM certificate chain (or TLS_CERT_FILE)")
    parser.add_argument("--tls-key", metavar="FILE", help="PEM private key (or TLS_KEY_FILE)")
    arguments = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    cert = arguments.tls_cert if arguments.tls_cert is not None else environment.get("TLS_CERT_FILE")
    key = arguments.tls_key if arguments.tls_key is not None else environment.get("TLS_KEY_FILE")
    if (cert is not None or key is not None) and (not cert or not key):
        raise SystemExit("TLS requires both --tls-cert/TLS_CERT_FILE and --tls-key/TLS_KEY_FILE to be non-empty")

    port_text = environment.get("PORT", str(DEFAULT_PORT)) if arguments.port is None else arguments.port
    try:
        port = int(port_text)
    except ValueError as error:
        raise SystemExit("PORT must be an integer") from error

    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")
    return port, cert, key


def resolve_port(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve and validate the listening port from CLI arguments and the environment."""
    return resolve_configuration(argv, environ)[0]


def create_tls_context(cert: str | None, key: str | None) -> ssl.SSLContext | None:
    """Load optional server credentials synchronously, reporting only safe configuration errors."""
    if cert is None and key is None:
        return None
    if not cert or not key:
        raise SystemExit("TLS requires both a non-empty certificate file and private key file")

    def reject_password() -> str:
        # Never let OpenSSL prompt on stdin during unattended startup.
        raise ValueError("Encrypted private keys are not supported")

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        context.load_cert_chain(cert, key, password=reject_password)
    except (OSError, ValueError):
        raise SystemExit(
            "TLS setup failed: check certificate/key readability, valid PEM, matching key, and an unencrypted key"
        ) from None
    return context


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
    except (ConnectionError, ssl.SSLError):
        pass
    finally:
        writer.close()
        with suppress(ConnectionError, ssl.SSLError, asyncio.TimeoutError):
            await writer.wait_closed()


async def serve(port: int, tls_context: ssl.SSLContext | None = None) -> None:
    """Run one HTTP or HTTPS listener until it is stopped."""
    if tls_context is None:
        server = await asyncio.start_server(handle_client, "0.0.0.0", port)
    else:
        server = await asyncio.start_server(
            handle_client,
            "0.0.0.0",
            port,
            ssl=tls_context,
            ssl_handshake_timeout=TLS_TIMEOUT_SECONDS,
            ssl_shutdown_timeout=TLS_TIMEOUT_SECONDS,
        )
    print(f"Listening on 0.0.0.0:{port}", flush=True)
    async with server:
        await server.serve_forever()


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Run the server, exiting cleanly when interrupted by the user."""
    port, cert, key = resolve_configuration(argv, environ)
    tls_context = create_tls_context(cert, key)
    try:
        asyncio.run(serve(port, tls_context))
    except KeyboardInterrupt:
        pass
    except OSError:
        raise SystemExit("Server startup or transport failed: check listening address and port availability") from None


if __name__ == "__main__":
    main()
