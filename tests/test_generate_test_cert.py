"""Exercise an isolated script copy with stub OpenSSL only; never generate real keys."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GenerateTestCertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        self.script = scripts / "generate-test-cert.sh"
        shutil.copyfile(ROOT / "scripts/generate-test-cert.sh", self.script)
        self.script.chmod(0o755)
        binary = self.root / "bin"
        binary.mkdir()
        stub = binary / "openssl"
        stub.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$@" > "$STUB_LOG"\n'
            'printf "stub diagnostic\\n" >&2\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -keyout|-out) shift; printf "NOT A CREDENTIAL\\n" > "$1" ;;\n'
            '  esac\n'
            '  shift\n'
            'done\n'
            'exit "${STUB_STATUS:-0}"\n',
            encoding="ascii",
        )
        stub.chmod(0o755)
        self.log = self.root / "openssl-arguments"
        self.environment = {
            "PATH": f"{binary}:{os.defpath}",
            "STUB_LOG": str(self.log),
        }

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.script), *arguments], cwd="/tmp/opencode", env=self.environment,
            capture_output=True, text=True, timeout=5, check=False,
        )

    def test_generation_arguments_permissions_and_no_secret_output(self) -> None:
        result = self.run_script("headers.example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("NOT A CREDENTIAL", result.stdout)
        self.assertIn("self-signed TEST", result.stdout)
        self.assertIn("not trusted by Cloudflare Full (strict)", result.stdout)
        arguments = self.log.read_text().splitlines()
        self.assertEqual(arguments, [
            "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-batch", "-days", "7",
            "-subj", "/CN=headers.example.com", "-addext", "subjectAltName=DNS:headers.example.com",
            "-keyout", "certificates/private-key.pem", "-out", "certificates/fullchain.pem",
        ])
        self.assertEqual(stat.S_IMODE((self.root / "certificates").stat().st_mode), 0o700)
        for name in ("private-key.pem", "fullchain.pem"):
            self.assertEqual(stat.S_IMODE((self.root / "certificates" / name).stat().st_mode), 0o600)
        self.assertNotEqual(self.run_script("headers.example.com").returncode, 0)

    def test_invalid_arguments_never_reach_openssl_or_create_certificates(self) -> None:
        for arguments in (
            (), ("example.com", "extra"), ("",), ("-example.com",), ("a..b",), ("a.",),
            ("a/b",), ("a,CN=evil",), ("a;touch bad",), ("a\nDNS:evil",), ("a b",),
            ("a_foo",), ("*.example.com",), ("é.example",), ("foo-.example",),
            ("a" * 65,), ("a" * 64,),
        ):
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.run_script(*arguments).returncode, 0)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.root / "certificates").exists())

    def test_existing_partial_output_is_untouched(self) -> None:
        certs = self.root / "certificates"
        certs.mkdir(parents=True)
        marker = certs / "private-key.pem"
        marker.write_text("stub sentinel, not a key")
        before = marker.stat()
        self.assertNotEqual(self.run_script("localhost").returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertEqual(marker.stat(), before)
        self.assertFalse((certs / "fullchain.pem").exists())

    def test_existing_empty_directory_is_refused(self) -> None:
        (self.root / "certificates").mkdir()
        self.assertNotEqual(self.run_script("localhost").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_existing_certificate_without_key_is_refused(self) -> None:
        certs = self.root / "certificates"
        certs.mkdir(parents=True)
        marker = certs / "fullchain.pem"
        marker.write_text("stub certificate sentinel")
        before = marker.stat()
        self.assertNotEqual(self.run_script("localhost").returncode, 0)
        self.assertEqual(marker.stat(), before)
        self.assertFalse(self.log.exists())
        self.assertFalse((certs / "private-key.pem").exists())

    def test_certificate_directory_symlink_is_refused(self) -> None:
        (self.root / "certificates").symlink_to(self.root / "bin", target_is_directory=True)
        self.assertNotEqual(self.run_script("localhost").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_dangling_symlink_is_refused(self) -> None:
        (self.root / "certificates").symlink_to(self.root / "missing", target_is_directory=True)
        self.assertNotEqual(self.run_script("localhost").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failure_retains_output_and_refuses_retry(self) -> None:
        self.environment["STUB_STATUS"] = "1"
        result = self.run_script("localhost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial certificates directory is retained", result.stderr)
        self.assertNotIn("stub diagnostic", result.stderr)
        self.assertTrue((self.root / "certificates/private-key.pem").exists())
        self.assertNotEqual(self.run_script("localhost").returncode, 0)

    def test_concurrent_invocations_have_one_winner(self) -> None:
        # Both processes compete to reserve the same project-level directory.
        processes = [subprocess.Popen(
            [str(self.script), "localhost"], cwd=self.root, env=self.environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(2)]
        for process in processes:
            process.communicate(timeout=5)
        self.assertEqual(sorted(process.returncode for process in processes), [0, 1])

    def test_documentation_and_script_mode(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertIn("./scripts/generate-test-cert.sh headers.example.com", readme)
        self.assertIn("not compatible with Cloudflare Full (strict)", readme)
        self.assertIn("--cacert certificates/fullchain.pem", readme)
        self.assertIn("SSL/TLS -> Origin Server -> Create Certificate", readme)
        self.assertIn("Let's Encrypt via DNS-01", readme)
        self.assertIn("even an empty directory", readme)
        self.assertTrue((ROOT / "scripts/generate-test-cert.sh").stat().st_mode & stat.S_IXUSR)
