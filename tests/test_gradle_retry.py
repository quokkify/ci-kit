from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "actions/gradle-retry/gradle-retry.sh"
ACTION = ROOT / "actions/gradle-retry/action.yml"


class GradleRetryTests(unittest.TestCase):
    def run_case(self, mode: str, *, max_attempts: str | None = "3", persistent: bool = False, delay: str | None = "0") -> tuple[subprocess.CompletedProcess[str], int, list[Path]]:
        """Run the script against a fake Gradle; ``None`` leaves an input unset so the script default applies.

        A ``sleep`` shim on PATH records each requested delay in ``self.sleeps`` instead of waiting,
        and ``self.calls_args`` holds the arguments of every fake Gradle invocation.
        """
        with tempfile.TemporaryDirectory(prefix="gradle-retry-test-") as temporary:
            root = Path(temporary)
            fake = root / "fake-gradle"
            count = root / "count"
            args_log = root / "args"
            sleep_log = root / "sleeps"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            sleep_shim = bin_dir / "sleep"
            sleep_shim.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$1\" >> \"$SLEEP_FILE\"\n")
            sleep_shim.chmod(sleep_shim.stat().st_mode | stat.S_IXUSR)
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "n=0; [[ -f \"$COUNT_FILE\" ]] && n=$(<\"$COUNT_FILE\")\n"
                "n=$((n + 1)); printf '%s' \"$n\" > \"$COUNT_FILE\"\n"
                "printf '%s\\n' \"$*\" >> \"$ARGS_FILE\"\n"
                "central_not_found() { echo '   > Could not find org.awaitility:awaitility:4.3.0.'; echo '     Searched in the following locations:'; echo '       - https://repo.maven.apache.org/maven2/org/awaitility/awaitility/4.3.0/awaitility-4.3.0.pom'; echo '     Required by:'; echo \"         project ':selenide'\"; }\n"
                "portal_not_found() { echo '   > Could not find org.example:portal-plugin:1.0.'; echo '     Searched in the following locations:'; echo '       - https://plugins.gradle.org/m2/org/example/portal-plugin/1.0/portal-plugin-1.0.pom'; }\n"
                "private_not_found() { echo '   > Could not find org.example:internal:1.0.'; echo '     Searched in the following locations:'; echo '       - https://maven.example.invalid/releases/org/example/internal/1.0/internal-1.0.pom'; }\n"
                "plugin_unresolved() { echo \"Plugin [id: 'com.vanniktech.maven.publish', version: '0.37.0', apply: false] was not found in any of the following sources:\"; echo; echo \"- Gradle Core Plugins (plugin is not in 'org.gradle' namespace)\"; echo '- Included Builds (No included builds contain this plugin)'; echo \"- Plugin Repositories (could not resolve plugin artifact 'com.vanniktech.maven.publish:com.vanniktech.maven.publish.gradle.plugin:0.37.0')\"; echo '  Searched in the following repositories:'; echo '    Gradle Central Plugin Repository'; }\n"
                "case \"$MODE\" in\n"
                "  ordinary) echo 'compilation failed'; exit 9;;\n"
                "  dsl) echo 'Could not find method implementation() for arguments'; exit 11;;\n"
                "  forbidden) echo 'Forbidden'; exit 12;;\n"
                "  server-error) echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 500 from server: Internal Server Error\"; exit 13;;\n"
                "  not-found) if [[ \"$*\" == *--refresh-dependencies* ]]; then [[ \"${PERSISTENT:-0}\" == 1 ]] && { echo 'Could not find org.example:missing:1.0.'; echo 'Searched in the following locations:'; exit 7; }; echo success; exit 0; fi; echo 'Could not find org.example:missing:1.0.'; echo 'Searched in the following locations:'; exit 7;;\n"
                "  rate-limit) [[ $n -lt 2 ]] && { echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 429 from server: Too Many Requests\"; exit 8; }; echo success; exit 0;;\n"
                "  persistent-rate-limit) echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 429 from server: Too Many Requests\"; exit $((20 + n));;\n"
                "  access-denied) [[ $n -lt 2 ]] && { echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 403 from server: Forbidden\"; exit 14; }; echo success; exit 0;;\n"
                "  gradle-rate-limit) [[ $n -lt 2 ]] && { echo \"            > Could not GET 'https://repo.maven.apache.org/maven2/org/awaitility/awaitility/4.3.0/awaitility-4.3.0.pom'.\"; echo '               > Received status code 429 from server: Too Many Requests'; exit 8; }; echo success; exit 0;;\n"
                "  gradle-access-denied) [[ $n -lt 2 ]] && { echo \"            > Could not HEAD 'https://repo.example.invalid/missing.pom'.\"; echo '               > Received status code 403 from server: Forbidden'; exit 14; }; echo success; exit 0;;\n"
                "  non-adjacent-status) echo \"            > Could not GET 'https://repo.example.invalid/missing.pom'.\"; echo '               > Received status code 500 from server: Internal Server Error'; echo '               > Received status code 429 from server: Too Many Requests'; exit 17;;\n"
                "  detached-status) echo 'Received status code 429 from server: Too Many Requests'; exit 16;;\n"
                "  plugin-unresolved) [[ $n -lt 2 ]] && { plugin_unresolved; exit 18; }; echo success; exit 0;;\n"
                "  plugin-stacktrace) [[ $n -lt 2 ]] && { echo \"org.gradle.api.plugins.UnknownPluginException: Plugin [id: 'org.example.plugin', version: '1.0'] was not found in any of the following sources:\"; echo \"- Plugin Repositories (could not resolve plugin artifact 'org.example.plugin:org.example.plugin.gradle.plugin:1.0')\"; exit 22; }; echo success; exit 0;;\n"
                "  persistent-plugin-unresolved) plugin_unresolved; exit $((30 + n));;\n"
                "  not-found-then-plugin) if [[ $n -eq 1 ]]; then echo 'Could not find org.example:missing:1.0.'; echo 'Searched in the following locations:'; exit 7; fi; [[ $n -eq 2 && \"$*\" == *--refresh-dependencies* ]] && { plugin_unresolved; exit 18; }; [[ \"$*\" != *--refresh-dependencies* ]] && { echo success; exit 0; }; exit 99;;\n"
                "  plugin-not-in-sources) echo \"Plugin [id: 'org.example.missing'] was not found in any of the following sources:\"; echo \"- Gradle Core Plugins (plugin is not in 'org.gradle' namespace)\"; echo '- Plugin Repositories (plugin dependency must include a version number for this source)'; exit 20;;\n"
                "  detached-plugin-artifact) echo \"- Plugin Repositories (could not resolve plugin artifact 'org.example:org.example.gradle.plugin:1.0')\"; exit 21;;\n"
                "  central-not-found) central_not_found; exit 7;;\n"
                "  central-not-found-recovers) [[ $n -lt 3 ]] && { central_not_found; exit 7; }; echo success; exit 0;;\n"
                "  central-not-found-refresh-plugin) [[ $n -eq 1 ]] && { central_not_found; exit 7; }; [[ \"$*\" == *--refresh-dependencies* ]] && { plugin_unresolved; exit 18; }; echo success; exit 0;;\n"
                "  portal-not-found) [[ $n -lt 2 ]] && { portal_not_found; exit 7; }; echo success; exit 0;;\n"
                "  private-not-found) private_not_found; exit 7;;\n"
                "  central-url-without-not-found) echo 'compilation failed'; echo '       - https://repo.maven.apache.org/maven2/org/example/a/1.0/a-1.0.pom'; exit 9;;\n"
                "  compile-error) echo \"/w/src/main/java/A.java:3: error: cannot find symbol\"; echo \"Execution failed for task ':compileJava'.\"; echo '> Compilation failed; see the compiler output below.'; exit 1;;\n"
                "  test-failure) echo 'ATest > works() FAILED'; echo \"Execution failed for task ':test'.\"; echo '> There were failing tests. See the report at: file:///w/build/reports/tests/test/index.html'; exit 1;;\n"
                "  persistent-access-denied) echo \"Could not GET 'https://repo.example.invalid/missing'. Received status code 403 from server: Forbidden\"; exit 15;;\n"
                "esac\n"
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "ARGS_FILE": str(args_log),
                "COUNT_FILE": str(count),
                "GRADLE_RETRY_COMMAND": f"{fake} --task test",
                "MODE": mode,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "SLEEP_FILE": str(sleep_log),
            }
            env.pop("GRADLE_RETRY_INITIAL_DELAY_SECONDS", None)
            env.pop("GRADLE_RETRY_MAX_ATTEMPTS", None)
            if delay is not None:
                env["GRADLE_RETRY_INITIAL_DELAY_SECONDS"] = delay
            if max_attempts is not None:
                env["GRADLE_RETRY_MAX_ATTEMPTS"] = max_attempts
            if mode == "not-found" and persistent:
                env["PERSISTENT"] = "1"
            result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False)
            leftovers = list(root.glob("gradle-retry.*"))
            self.calls_args = args_log.read_text().splitlines() if args_log.exists() else []
            self.sleeps = [int(value) for value in sleep_log.read_text().split()] if sleep_log.exists() else []
            return result, int(count.read_text()) if count.exists() else 0, leftovers

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

    def test_persistent_429_fails_with_last_exit_code(self) -> None:
        result, calls, _ = self.run_case("persistent-rate-limit", max_attempts="3")
        self.assertEqual(calls, 3)
        self.assertEqual(result.returncode, 23)
        self.assertIn("retry budget exhausted after 3/3 attempts", result.stdout)

    def test_malformed_inputs_are_rejected_before_running_command(self) -> None:
        for max_attempts, delay in (("0", "0"), ("abc", "0"), ("-1", "0"), ("2.5", "0"), ("3", "-1"), ("3", "x")):
            with self.subTest(max_attempts=max_attempts, delay=delay):
                result, calls, _ = self.run_case("ordinary", max_attempts=max_attempts, delay=delay)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(calls, 0)

    def test_unrelated_forbidden_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("forbidden")
        self.assertEqual(result.returncode, 12)
        self.assertEqual(calls, 1)

    def test_http_500_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("server-error")
        self.assertEqual(result.returncode, 13)
        self.assertEqual(calls, 1)

    def test_gradle_two_line_429_is_retried(self) -> None:
        """Regression guard: Gradle prints the URL and the status on separate lines."""
        result, calls, _ = self.run_case("gradle-rate-limit", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("HTTP 429", result.stdout)

    def test_gradle_two_line_403_is_retried(self) -> None:
        """Regression guard: Gradle prints the URL and the status on separate lines."""
        result, calls, _ = self.run_case("gradle-access-denied", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("HTTP 403", result.stdout)

    def test_status_without_repository_request_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("detached-status")
        self.assertEqual(result.returncode, 16)
        self.assertEqual(calls, 1)

    def test_status_not_adjacent_to_request_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("non-adjacent-status")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(calls, 1)

    def test_plugin_resolution_failure_is_retried(self) -> None:
        """Regression guard: a plugin portal outage surfaces as UnknownPluginException without an HTTP status."""
        result, calls, _ = self.run_case("plugin-unresolved", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("plugin resolution", result.stdout)
        self.assertNotIn("refresh-dependencies", result.stdout)

    def test_stacktrace_plugin_resolution_failure_is_retried(self) -> None:
        result, calls, _ = self.run_case("plugin-stacktrace", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("plugin resolution", result.stdout)

    def test_persistent_plugin_resolution_failure_is_bounded(self) -> None:
        result, calls, _ = self.run_case("persistent-plugin-unresolved", max_attempts="3")
        self.assertEqual(calls, 3)
        self.assertEqual(result.returncode, 33)
        self.assertIn("retry budget exhausted after 3/3 attempts", result.stdout)

    def test_plugin_failure_after_refresh_is_retried(self) -> None:
        """Regression guard: the refresh retry can itself hit a plugin portal outage."""
        result, calls, _ = self.run_case("not-found-then-plugin", max_attempts="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 3)
        self.assertEqual([("--refresh-dependencies" in args) for args in self.calls_args], [False, True, False])

    def test_plugin_missing_without_artifact_resolution_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("plugin-not-in-sources")
        self.assertEqual(result.returncode, 20)
        self.assertEqual(calls, 1)

    def test_plugin_artifact_line_without_unknown_plugin_header_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("detached-plugin-artifact")
        self.assertEqual(result.returncode, 21)
        self.assertEqual(calls, 1)

    def test_refresh_dependencies_is_used_for_one_attempt_then_dropped(self) -> None:
        """Regression guard (q4j #899): a sticky --refresh-dependencies re-fetched every plugin from a throttled Central on each retry."""
        result, calls, _ = self.run_case("central-not-found-refresh-plugin", max_attempts="3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 3)
        self.assertEqual([("--refresh-dependencies" in args) for args in self.calls_args], [False, True, False])
        self.assertEqual(self.calls_args[2], "--task test")

    def test_persistent_central_not_found_refreshes_once_and_fails_with_gradle_error(self) -> None:
        """Regression guard (q4j #902): Central throttling surfaces as "Could not find" for an artifact that exists."""
        result, calls, _ = self.run_case("central-not-found", max_attempts="3")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(calls, 3)
        self.assertEqual([("--refresh-dependencies" in args) for args in self.calls_args], [False, True, False])
        self.assertEqual(len(self.sleeps), 2)
        self.assertIn("repository not-found", result.stdout)
        self.assertIn("retry budget exhausted after 3/3 attempts", result.stdout)
        final_attempt = result.stdout.split("(attempt 2/3)")[-1]
        self.assertIn("Could not find org.awaitility:awaitility:4.3.0.", final_attempt)
        self.assertIn("https://repo.maven.apache.org/maven2/org/awaitility/awaitility/4.3.0/awaitility-4.3.0.pom", final_attempt)

    def test_central_not_found_recovers_on_later_attempt(self) -> None:
        result, calls, _ = self.run_case("central-not-found-recovers", max_attempts="4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 3)
        self.assertEqual([("--refresh-dependencies" in args) for args in self.calls_args], [False, True, False])
        self.assertEqual(len(self.sleeps), 2)

    def test_plugin_portal_not_found_is_retried_with_backoff(self) -> None:
        result, calls, _ = self.run_case("portal-not-found", max_attempts="2", delay="5")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 5)

    def test_private_repository_not_found_keeps_single_immediate_refresh(self) -> None:
        result, calls, _ = self.run_case("private-not-found", max_attempts="3", delay="5")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(calls, 2)
        self.assertEqual([("--refresh-dependencies" in args) for args in self.calls_args], [False, True])
        self.assertEqual(self.sleeps, [])

    def test_central_url_without_dependency_not_found_is_not_retried(self) -> None:
        result, calls, _ = self.run_case("central-url-without-not-found")
        self.assertEqual(result.returncode, 9)
        self.assertEqual(calls, 1)

    def test_build_failures_are_not_retried_with_default_budget(self) -> None:
        for mode in ("compile-error", "test-failure"):
            with self.subTest(mode=mode):
                result, calls, _ = self.run_case(mode, max_attempts=None, delay=None)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(calls, 1)
                self.assertEqual(self.sleeps, [])
                self.assertNotIn("refresh-dependencies", result.stdout)

    def test_default_backoff_outlasts_a_four_minute_throttle_window(self) -> None:
        """Regression guard (q4j #901): 429 windows lasted 3+ minutes, longer than the old 30s + 90s budget."""
        result, calls, _ = self.run_case("persistent-rate-limit", max_attempts=None, delay=None)
        self.assertEqual(calls, 4)
        self.assertEqual(result.returncode, 24)
        self.assertEqual(len(self.sleeps), 3)
        for sleep, base in zip(self.sleeps, (20, 60, 180)):
            self.assertGreaterEqual(sleep, base)
            self.assertLessEqual(sleep, base + base // 3)
        self.assertGreaterEqual(sum(self.sleeps), 240)

    def test_action_defaults_match_script_defaults(self) -> None:
        inputs = yaml.safe_load(ACTION.read_text())["inputs"]
        self.assertEqual(inputs["max-attempts"]["default"], "4")
        self.assertEqual(inputs["initial-delay-seconds"]["default"], "20")
        script = SCRIPT.read_text()
        self.assertIn('max_attempts="${GRADLE_RETRY_MAX_ATTEMPTS:-4}"', script)
        self.assertIn('delay="${GRADLE_RETRY_INITIAL_DELAY_SECONDS:-20}"', script)


if __name__ == "__main__":
    unittest.main()
