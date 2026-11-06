from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "actions/gradle-retry/gradle-retry.sh"


class GradleRetryTests(unittest.TestCase):
    def run_case(self, mode: str, *, max_attempts: str = "3", persistent: bool = False) -> tuple[subprocess.CompletedProcess[str], int, list[Path]]:
        with tempfile.TemporaryDirectory(prefix="gradle-retry-test-") as temporary:
            root = Path(temporary)
            fake = root / "fake-gradle"
            count = root / "count"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "n=0; [[ -f \"$COUNT_FILE\" ]] && n=$(<\"$COUNT_FILE\")\n"
                "n=$((n + 1)); printf '%s' \"$n\" > \"$COUNT_FILE\"\n"
                "case \"$MODE\" in\n"
                "  ordinary) echo 'compilation failed'; exit 9;;\n"
                "  dsl) echo 'Could not find method implementation() for arguments'; exit 11;;\n"
                "  forbidden) echo 'Forbidden'; exit 12;;\n"
                "  server-error) echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 500 from server: Internal Server Error\"; exit 13;;\n"
                "  not-found) if [[ \"$*\" == *--refresh-dependencies* ]]; then [[ \"${PERSISTENT:-0}\" == 1 ]] && { echo 'Could not find org.example:missing:1.0.'; echo 'Searched in the following locations:'; exit 7; }; echo success; exit 0; fi; echo 'Could not find org.example:missing:1.0.'; echo 'Searched in the following locations:'; exit 7;;\n"
                "  rate-limit) [[ $n -lt 2 ]] && { echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 429 from server: Too Many Requests\"; exit 8; }; echo success; exit 0;;\n"
                "  access-denied) [[ $n -lt 2 ]] && { echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 403 from server: Forbidden\"; exit 14; }; echo success; exit 0;;\n"
                "  persistent-access-denied) echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 403 from server: Forbidden\"; exit 15;;\n"
                "esac\n"
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "COUNT_FILE": str(count),
                "GRADLE_RETRY_COMMAND": f"{fake} --task test",
                "GRADLE_RETRY_INITIAL_DELAY_SECONDS": "0",
                "GRADLE_RETRY_MAX_ATTEMPTS": max_attempts,
                "MODE": mode,
            }
            if mode == "not-found" and persistent:
                env["PERSISTENT"] = "1"
            result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False)
            leftovers = list(root.glob("gradle-retry.*"))
            return result, int(count.read_text()), leftovers

    def test_successful_run_does_not_refresh(self) -> None:
        result, calls, leftovers = self.run_case("success")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, 1)
        self.assertNotIn("refresh-dependencies", result.stdout)
        self.assertEqual(leftovers, [])

    def test_ordinary_failure_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("ordinary")
        self.assertEqual(result.returncode, 9)
        self.assertEqual(calls, 1)

    def test_dsl_not_found_is_not_retried_or_refreshed(self) -> None:
        result, calls, _ = self.run_case("dsl")
        self.assertEqual(result.returncode, 11)
        self.assertEqual(calls, 1)
        self.assertNotIn("refresh-dependencies", result.stdout)

    def test_not_found_gets_exactly_one_refresh_retry(self) -> None:
        result, calls, _ = self.run_case("not-found")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("--refresh-dependencies", result.stdout)

    def test_persistent_not_found_fails_after_refresh(self) -> None:
        result, calls, _ = self.run_case("not-found", persistent=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(calls, 2)

    def test_429_keeps_bounded_backoff_retry(self) -> None:
        result, calls, _ = self.run_case("rate-limit", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertNotIn("refresh-dependencies", result.stdout)

    def test_transient_403_recovers_once(self) -> None:
        result, calls, _ = self.run_case("access-denied", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("HTTP 403", result.stdout)

    def test_persistent_403_is_bounded_and_nonzero(self) -> None:
        result, calls, _ = self.run_case("persistent-access-denied", max_attempts="3")
        self.assertEqual(result.returncode, 15)
        self.assertEqual(calls, 3)
        self.assertIn("retry budget exhausted after 3/3 attempts", result.stdout)

    def test_unrelated_forbidden_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("forbidden")
        self.assertEqual(result.returncode, 12)
        self.assertEqual(calls, 1)

    def test_http_500_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("server-error")
        self.assertEqual(result.returncode, 13)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
