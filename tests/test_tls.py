"""TLS configuration, safe startup, and real decrypted-header roundtrip tests."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import ssl
import subprocess
import tempfile
import unittest
from collections.abc import Coroutine
from contextlib import redirect_stdout
from pathlib import Path
from typing import Never
from unittest.mock import AsyncMock, MagicMock, patch

from app import (
    DEFAULT_PORT,
    TLS_TIMEOUT_SECONDS,
    create_tls_context,
    handle_client,
    main,
    resolve_configuration,
    resolve_port,
    serve,
)


class TLSConfigurationTests(unittest.TestCase):
    def test_plain_defaults(self) -> None:
        self.assertEqual(resolve_configuration([], {}), (DEFAULT_PORT, None, None))
        self.assertIsNone(create_tls_context(None, None))

    def test_environment_and_per_option_cli_precedence(self) -> None:
        environment = {"TLS_CERT_FILE": "env-cert", "TLS_KEY_FILE": "env-key"}
        self.assertEqual(resolve_configuration([], environment), (8080, "env-cert", "env-key"))
        self.assertEqual(
            resolve_configuration(["--tls-cert", "cli-cert"], environment),
            (8080, "cli-cert", "env-key"),
        )
        argv = ["--port", "8443", "--tls-cert", "cli-cert", "--tls-key", "cli-key"]
        self.assertEqual(resolve_configuration(argv, environment), (8443, "cli-cert", "cli-key"))
        self.assertEqual(resolve_port(argv, environment), 8443)

    def test_incomplete_and_empty_configuration(self) -> None:
        for argv, environment in (
            (["--tls-cert", "cert"], {}),
            (["--tls-key", "key"], {}),
            ([], {"TLS_CERT_FILE": "cert"}),
            ([], {"TLS_KEY_FILE": "key"}),
            ([], {"TLS_CERT_FILE": "", "TLS_KEY_FILE": ""}),
            (["--tls-key", ""], {"TLS_CERT_FILE": "cert", "TLS_KEY_FILE": "key"}),
        ):
            with self.subTest(argv=argv, environment=environment):
                with self.assertRaisesRegex(SystemExit, "TLS requires both"), patch("app.asyncio.run") as run:
                    main(argv, environment)
                run.assert_not_called()

    def test_context_options_and_noninteractive_key_loading(self) -> None:
        with patch("app.ssl.SSLContext") as constructor:
            context = create_tls_context("cert", "key")
        constructor.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        context.set_alpn_protocols.assert_called_once_with(["http/1.1"])
        args, kwargs = context.load_cert_chain.call_args
        self.assertEqual(args, ("cert", "key"))
        with self.assertRaisesRegex(ValueError, "Encrypted private keys"):
            kwargs["password"]()

    def test_loading_errors_are_safe_and_precede_event_loop(self) -> None:
        for error in (FileNotFoundError("sensitive"), PermissionError("sensitive"),
                      ssl.SSLError("sensitive"), ValueError("sensitive")):
            with self.subTest(error=type(error)), patch("app.ssl.SSLContext") as constructor:
                constructor.return_value.load_cert_chain.side_effect = error
                with patch("app.asyncio.run") as run, self.assertRaises(SystemExit) as raised:
                    main([], {"TLS_CERT_FILE": "cert", "TLS_KEY_FILE": "key"})
                self.assertIn("TLS setup failed", str(raised.exception))
                self.assertNotIn("sensitive", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                run.assert_not_called()

    def test_bind_errors_are_safe(self) -> None:
        def fail(coroutine: Coroutine[object, object, None]) -> Never:
            coroutine.close()
            raise OSError("sensitive")

        with patch("app.asyncio.run", side_effect=fail), self.assertRaises(SystemExit) as raised:
            main([], {})
        self.assertIn("port availability", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))


class TLSListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_listener_options_only_apply_to_tls(self) -> None:
        for context in (None, ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)):
            server = MagicMock()
            server.__aenter__ = AsyncMock(return_value=server)
            server.__aexit__ = AsyncMock(return_value=False)
            server.serve_forever = AsyncMock()
            with patch("app.asyncio.start_server", new_callable=AsyncMock, return_value=server) as start:
                with redirect_stdout(io.StringIO()):
                    await serve(8443, context)
            expected = {} if context is None else {
                "ssl": context,
                "ssl_handshake_timeout": TLS_TIMEOUT_SECONDS,
                "ssl_shutdown_timeout": TLS_TIMEOUT_SECONDS,
            }
            start.assert_awaited_once_with(handle_client, "0.0.0.0", 8443, **expected)

    async def test_routine_tls_disconnect_and_shutdown_errors_are_suppressed(self) -> None:
        for error in (ssl.SSLError("disconnect"), ConnectionAbortedError("disconnect")):
            reader = AsyncMock()
            reader.read.side_effect = error
            writer = MagicMock()
            writer.wait_closed = AsyncMock(side_effect=error)
            await handle_client(reader, writer)
            writer.close.assert_called_once()
        reader = AsyncMock()
        reader.read.return_value = b"GET / HTTP/1.1\r\n\r\n"
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock(side_effect=TimeoutError)
        await handle_client(reader, writer)


@unittest.skipUnless(shutil.which("openssl"), "openssl is required for ephemeral test certificates")
class TLSRoundtripTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.cert = str(Path(cls.directory.name) / "cert.pem")
        cls.key = str(Path(cls.directory.name) / "key.pem")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
             "-keyout", cls.key, "-out", cls.cert],
            check=True, capture_output=True, timeout=15,
        )

    async def test_actual_tls_preserves_decrypted_header_bytes(self) -> None:
        context = create_tls_context(self.cert, self.key)
        server = await asyncio.start_server(
            handle_client, "127.0.0.1", 0, ssl=context,
            ssl_handshake_timeout=TLS_TIMEOUT_SECONDS, ssl_shutdown_timeout=TLS_TIMEOUT_SECONDS,
        )
        client = ssl.create_default_context(cafile=self.cert)
        client.set_alpn_protocols(["h2", "http/1.1"])
        raw_headers = (
            b"Host: localhost\r\nx-lower: first\r\nX-Duplicate: one\r\n"
            b"X-Duplicate: two\r\nOdd-CASE:\t spaced  \r\n"
        )
        async with server:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    "127.0.0.1", server.sockets[0].getsockname()[1], ssl=client, server_hostname="localhost",
                ), timeout=5,
            )
            try:
                self.assertEqual(writer.get_extra_info("ssl_object").selected_alpn_protocol(), "http/1.1")
                writer.write(b"GET / HTTP/1.1\r\n" + raw_headers + b"\r\n")
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), timeout=5)
                head, separator, body = response.partition(b"\r\n\r\n")
                self.assertTrue(head.startswith(b"HTTP/1.1 200 OK\r\n"))
                self.assertEqual(separator, b"\r\n\r\n")
                self.assertEqual(body, raw_headers)
            finally:
                writer.close()
                await writer.wait_closed()

    def test_real_missing_and_invalid_credentials_fail_safely(self) -> None:
        invalid = Path(self.directory.name) / "invalid.pem"
        invalid.write_text("test-only invalid PEM", encoding="ascii")
        for cert, key in ((self.cert + ".missing", self.key), (str(invalid), self.key)):
            with self.subTest(cert=cert), self.assertRaisesRegex(SystemExit, "TLS setup failed"):
                create_tls_context(cert, key)

    def test_real_mismatched_and_encrypted_keys_fail_without_prompt(self) -> None:
        for encrypted in (False, True):
            key = str(Path(self.directory.name) / f"other-{encrypted}.pem")
            command = ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key]
            if encrypted:
                command.extend(["-aes-256-cbc", "-pass", "pass:test-only-password"])
            subprocess.run(command, check=True, capture_output=True, timeout=15)
            # A subprocess timeout also verifies that encrypted keys never prompt for input.
            result = self._run_entrypoint(self.cert, key)
            self.assertEqual(result.returncode, 1)
            self.assertIn("TLS setup failed", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("test-only-password", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_default_entrypoint_uses_tls_environment(self) -> None:
        result = self._run_entrypoint(self.cert + ".missing", self.key)
        self.assertEqual(result.returncode, 1)
        self.assertIn("TLS setup failed", result.stderr)
        self.assertNotIn(self.cert, result.stderr)
        self.assertEqual(result.stdout, "")

    def _run_entrypoint(self, cert: str, key: str) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [str(root / "entrypoint.sh")], cwd=root,
            env={"PATH": os.defpath, "TLS_CERT_FILE": cert, "TLS_KEY_FILE": key},
            check=False, capture_output=True, text=True, timeout=5,
        )
