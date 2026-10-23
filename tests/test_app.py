"""Integration tests for the raw-header server."""

from __future__ import annotations

import asyncio
import functools
import io
import unittest
from collections.abc import Coroutine
from contextlib import redirect_stderr
from typing import Never
from unittest.mock import patch

from app import DEFAULT_PORT, handle_client, main, resolve_port


class MainTests(unittest.TestCase):
    def test_keyboard_interrupt_from_asyncio_runner_is_suppressed(self) -> None:
        def interrupt(coroutine: Coroutine[object, object, None]) -> Never:
            coroutine.close()
            raise KeyboardInterrupt

        with patch("app.asyncio.run", side_effect=interrupt) as run:
            main(["--port", "9000"], {})

        run.assert_called_once()


class PortResolutionTests(unittest.TestCase):
    def test_cli_port_takes_precedence_over_environment(self) -> None:
        self.assertEqual(resolve_port(["--port", "9000"], {"PORT": "invalid"}), 9000)

    def test_environment_port_is_used_without_cli_option(self) -> None:
        self.assertEqual(resolve_port([], {"PORT": "7000"}), 7000)

    def test_default_port_is_used_without_configuration(self) -> None:
        self.assertEqual(resolve_port([], {}), DEFAULT_PORT)

    def test_port_range_boundaries_are_valid(self) -> None:
        self.assertEqual(resolve_port(["--port", "1"], {}), 1)
        self.assertEqual(resolve_port([], {"PORT": "65535"}), 65535)

    def test_invalid_cli_port_is_reported_by_argparse(self) -> None:
        for value, message in (
            ("not-an-integer", "PORT must be an integer"),
            ("0", "PORT must be between 1 and 65535"),
            ("65536", "PORT must be between 1 and 65535"),
        ):
            with self.subTest(value=value), redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaisesRegex(SystemExit, "2"):
                    resolve_port(["--port", value], {})
                self.assertIn(f"argument --port: {message}", stderr.getvalue())

    def test_invalid_environment_port_has_clear_error(self) -> None:
        for value, message in (
            ("not-an-integer", "PORT must be an integer"),
            ("0", "PORT must be between 1 and 65535"),
            ("65536", "PORT must be between 1 and 65535"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SystemExit, message):
                    resolve_port([], {"PORT": value})


class HeaderServerTests(unittest.IsolatedAsyncioTestCase):
    async def _request(
        self,
        request: bytes,
        *,
        max_header_size: int = 16 * 1024,
        close_input: bool = False,
    ) -> tuple[int, dict[bytes, bytes], bytes]:
        handler = functools.partial(
            handle_client,
            max_header_size=max_header_size,
            timeout=0.5,
        )
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request)
            await writer.drain()
            if close_input:
                writer.write_eof()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

        response_head, separator, body = response.partition(b"\r\n\r\n")
        self.assertEqual(separator, b"\r\n\r\n")
        lines = response_head.split(b"\r\n")
        status = int(lines[0].split(b" ", 2)[1])
        headers = dict(line.split(b": ", 1) for line in lines[1:])
        self.assertEqual(int(headers[b"Content-Length"]), len(body))
        self.assertEqual(headers[b"Connection"], b"close")
        return status, headers, body

    async def test_preserves_order_duplicates_casing_whitespace_and_line_endings(self) -> None:
        raw_headers = (
            b"x-lower: first\r\n"
            b"X-Duplicate: one\r\n"
            b"X-Duplicate: two\r\n"
            b"Odd-CASE:\t spaced  \r\n"
        )
        request = b"GET / HTTP/1.1\r\n" + raw_headers + b"\r\nignored body"

        status, headers, body = await self._request(request)

        self.assertEqual(status, 200)
        self.assertEqual(headers[b"Content-Type"], b"text/plain")
        self.assertEqual(body, raw_headers)

    async def test_request_without_headers_has_an_empty_body(self) -> None:
        status, _, body = await self._request(b"GET / HTTP/1.0\r\n\r\n")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    async def test_missing_boundary_returns_safe_error(self) -> None:
        partial_secret = b"GET / HTTP/1.1\r\nAuthorization: secret"

        status, _, body = await self._request(partial_secret, close_input=True)

        self.assertEqual(status, 400)
        self.assertEqual(body, b"Bad Request\n")
        self.assertNotIn(b"secret", body)

    async def test_oversized_request_returns_safe_error(self) -> None:
        request = b"GET / HTTP/1.1\r\nX-Large: " + (b"a" * 80) + b"\r\n\r\n"

        status, _, body = await self._request(request, max_header_size=64)

        self.assertEqual(status, 431)
        self.assertEqual(body, b"Request Header Fields Too Large\n")
        self.assertNotIn(b"a" * 8, body)


if __name__ == "__main__":
    unittest.main()
