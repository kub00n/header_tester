"""Non-network tests for runtime selection and OpenResty configuration."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = PROJECT_ROOT / "entrypoint.sh"
PORT_ERROR = "PORT must contain 1 to 20 digits and be between 1 and 65535\n"


class EntrypointTests(unittest.TestCase):
    def _run(
        self,
        *,
        selector: str | None = None,
        port: str | None = None,
        stub: str = "python3",
    ) -> tuple[subprocess.CompletedProcess[str], str | None]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            stub_path = Path(temporary_directory) / stub
            stub_path.write_text(
                "#!/bin/sh\n"
                "printf 'stub=%s\\n' \"$(basename \"$0\")\"\n"
                "printf 'args=%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment["PATH"] = f"{temporary_directory}:{environment['PATH']}"
            environment["HEADER_SERVER_RUNTIME_PARENT"] = temporary_directory
            if selector is None:
                environment.pop("HEADER_SERVER", None)
            else:
                environment["HEADER_SERVER"] = selector
            if port is None:
                environment.pop("PORT", None)
            else:
                environment["PORT"] = port

            result = subprocess.run(
                [str(ENTRYPOINT)],
                cwd=PROJECT_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            rendered_config = None
            if stub == "openresty" and result.returncode == 0:
                config_match = re.search(r"(?:^| )-c ([^ ]+/nginx\.conf)", result.stdout)
                if config_match is not None:
                    rendered_config = Path(config_match.group(1)).read_text(encoding="utf-8")
            return result, rendered_config

    def test_defaults_to_python_without_forwarding_port_argument(self) -> None:
        result, _ = self._run(port="9000")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "stub=python3\nargs=app.py\n")

    def test_selects_openresty_and_renders_normalized_port(self) -> None:
        result, config = self._run(selector="openresty", port="008080", stub="openresty")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stub=openresty\n", result.stdout)
        self.assertIsNotNone(config)
        self.assertIn("listen 8080;", config)
        self.assertNotIn("{{PORT}}", config)

    def test_rejects_invalid_selector(self) -> None:
        for selector in ("", "gunicorn", "Python"):
            with self.subTest(selector=selector):
                result, _ = self._run(selector=selector)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "HEADER_SERVER must be python or openresty\n")

    def test_rejects_invalid_ports_before_runtime_selection(self) -> None:
        for port in ("", "abc", "1;echo injected", "0", "65536", "999999999999999999999"):
            with self.subTest(port=port):
                result, _ = self._run(port=port)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, PORT_ERROR)

    def test_accepts_port_range_boundaries(self) -> None:
        for port in ("1", "65535", "0" * 15 + "65535"):
            with self.subTest(port=port):
                result, _ = self._run(port=port)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_excessively_long_port_before_normalization(self) -> None:
        result, _ = self._run(port="0" * 20 + "1")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, PORT_ERROR)


class OpenRestySourceTests(unittest.TestCase):
    def test_lua_uses_raw_header_without_parsing_or_rebuilding(self) -> None:
        source = (PROJECT_ROOT / "openresty/raw_headers.lua").read_text(encoding="utf-8")

        self.assertEqual(source.count("ngx.req.raw_header("), 1)
        self.assertNotIn("ngx.req.get_headers", source)
        self.assertIn("MAX_REQUEST_HEAD_SIZE = 16 * 1024", source)
        self.assertIn("request_line_end + 2, raw_length - 2", source)

    def test_config_uses_bounded_header_buffers_and_standard_streams(self) -> None:
        config = (PROJECT_ROOT / "openresty/nginx.conf.template").read_text(encoding="utf-8")

        self.assertIn("client_header_buffer_size 1k;", config)
        self.assertIn("large_client_header_buffers 1 16k;", config)
        self.assertIn("client_max_body_size 0;", config)
        self.assertIn("keepalive_timeout 0;", config)
        self.assertIn("keepalive_requests 1;", config)
        self.assertIn("access_log /dev/stdout combined;", config)
        self.assertIn("error_log stderr notice;", config)

    def test_lua_does_not_consume_the_request_body(self) -> None:
        source = (PROJECT_ROOT / "openresty/raw_headers.lua").read_text(encoding="utf-8")

        self.assertNotIn("discard_body", source)
        self.assertNotIn("read_body", source)

    def test_readme_documents_single_request_and_unconsumed_body(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Both runtimes close the connection after one request.", readme)
        self.assertIn("does not read or discard the potentially unbounded request body", readme)

    def test_dockerfile_uses_non_fat_image_and_sigterm(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM openresty/openresty:1.31.1.1-5-bookworm\n", dockerfile)
        self.assertNotIn("bookworm-fat", dockerfile)
        self.assertIn("STOPSIGNAL SIGTERM", dockerfile)


if __name__ == "__main__":
    unittest.main()
