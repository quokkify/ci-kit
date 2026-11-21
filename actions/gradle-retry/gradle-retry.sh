#!/usr/bin/env bash
# Run a Gradle command with bounded recovery for transient resolution failures.
# Repository HTTP 403/429 failures, plugin artifact resolution failures and
# dependency not-found reports from Maven Central or the Gradle Plugin Portal
# (which is how Gradle reports throttling it cannot attribute) get exponential
# retry. A dependency not-found gets --refresh-dependencies on exactly one
# attempt; later attempts run the original command again.
set -uo pipefail

command_text() {
  local value="${1//"%"/%25}"
  value="${value//$'\r'/%0D}"
  printf '%s' "${value//$'\n'/%0A}"
}

command_string="${GRADLE_RETRY_COMMAND:-}"
max_attempts="${GRADLE_RETRY_MAX_ATTEMPTS:-4}"
delay="${GRADLE_RETRY_INITIAL_DELAY_SECONDS:-20}"

if [[ -z "$command_string" ]]; then
  echo "gradle-retry.sh: GRADLE_RETRY_COMMAND is required" >&2
  exit 2
fi
if ! [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
  echo "gradle-retry.sh: GRADLE_RETRY_MAX_ATTEMPTS must be a positive integer, got '$(command_text "$max_attempts")'" >&2
  exit 2
fi
if ! [[ "$delay" =~ ^[0-9]+$ ]]; then
  echo "gradle-retry.sh: GRADLE_RETRY_INITIAL_DELAY_SECONDS must be a non-negative integer, got '$(command_text "$delay")'" >&2
  exit 2
fi

# Several retry steps can run in one job under one job timeout. Steps of a
# job share RUNNER_TEMP, so the waits of all of them are capped together at
# one step's longest possible schedule, derived from the same inputs.
job_wait_budget=0
schedule_delay="$delay"
for (( step = 1; step < max_attempts; step++ )); do
  job_wait_budget=$(( job_wait_budget + schedule_delay + schedule_delay / 3 ))
  schedule_delay=$(( schedule_delay * 3 ))
done
job_wait_file=""
if [[ -n "${RUNNER_TEMP:-}" ]]; then
  job_wait_file="${RUNNER_TEMP}/gradle-retry-waited-seconds"
fi

attempt=1
refresh_attempted=0
refresh_next=0
output_file=""
cleanup() {
  if [[ -n "$output_file" ]]; then
    rm -f -- "$output_file"
  fi
}
trap cleanup EXIT INT TERM

while true; do
  output_file="$(mktemp "${TMPDIR:-/tmp}/gradle-retry.XXXXXX")"
  run_command="$command_string"
  if [[ "$refresh_next" -eq 1 ]]; then
    run_command="$command_string --refresh-dependencies"
    refresh_next=0
  fi
  bash -euo pipefail -c "$run_command" 2>&1 | tee "$output_file"
  status=${PIPESTATUS[0]}

  if [[ "$status" -eq 0 ]]; then
    exit 0
  fi

  # Match Gradle's repository diagnostic, not a bare "Forbidden" from an
  # unrelated task. The URL is deliberately constrained to a quoted value.
  # Gradle prints the request and its status on consecutive lines
  # ("> Could not GET '<url>'." then "> Received status code 429 ...");
  # the single-line form is accepted too. The last match wins.
  transient_status="$(awk -v q="'" '
    BEGIN { request = "Could not (GET|HEAD) " q "[^" q "]+" q "\\." }
    {
      if (pending && $0 ~ /^[[:space:]>]*Received status code (403|429)([[:space:]]|$)/) {
        match($0, /code (403|429)/); status = substr($0, RSTART + 5, 3)
      }
      if ($0 ~ (request " Received status code (403|429)([[:space:]]|$)")) {
        match($0, /code (403|429)/); status = substr($0, RSTART + 5, 3)
      }
      pending = ($0 ~ (request "[[:space:]]*$"))
    }
    END { print status }
  ' "$output_file")"
  # Gradle's dependency-resolution not-found report includes both the module
  # coordinates and a repository search section. Do not treat unrelated DSL
  # errors such as "Could not find method implementation()" as resolvable.
  is_dependency_not_found=0
  if grep -qE 'Could not find [^[:space:]]+:[^[:space:]]+:[^[:space:]]+' "$output_file" \
    && grep -qE 'Searched in( the following locations)?:' "$output_file"; then
    is_dependency_not_found=1
  fi
  # Maven Central and the Plugin Portal answer a throttled request in a way
  # Gradle reports as "Could not find", hiding the status. Only a location
  # listed in a search section counts, not a URL elsewhere in the output.
  public_not_found=0
  if [[ "$is_dependency_not_found" -eq 1 ]] && awk '
    /Searched in( the following locations)?:[[:space:]]*$/ { searching = 1; next }
    searching && /^[[:space:]]*- / {
      if ($0 ~ /^[[:space:]]*- https:\/\/(repo\.maven\.apache\.org|repo1\.maven\.org|plugins\.gradle\.org)\//) { found = 1 }
      next
    }
    { searching = 0 }
    END { exit !found }
  ' "$output_file"; then
    public_not_found=1
  fi

  # With --continue a run can report a repository failure next to a real
  # task failure. Re-running it would re-run tests and could hide a flaky
  # one, so a genuine test, compilation or verification failure disables
  # every recovery for this run.
  genuine_failure=0
  if grep -qE '(There were failing tests|Compilation failed|Compilation error\. See log|rule violations were found)' "$output_file"; then
    genuine_failure=1
    echo "::warning::Gradle reported a test, compilation or verification failure; repository failures in the same run are not retried."
  fi

  transient_reason=""
  if [[ "$genuine_failure" -eq 1 ]]; then
    :
  elif [[ -n "$transient_status" ]]; then
    transient_reason="repository HTTP ${transient_status}"
  # A plugin portal outage carries no HTTP status: Gradle reports an unknown
  # plugin whose repository lookup could not resolve the plugin artifact.
  # Both parts are required so a plugin missing from other sources, or a
  # stray artifact line, is not retried.
  elif grep -qE "^[[:space:]>]*(org\.gradle\.api\.plugins\.UnknownPluginException: )?Plugin \[id: '[^']+'.*\] was not found in any of the following sources:" "$output_file" \
    && grep -qE "^[[:space:]]*- Plugin Repositories \(could not resolve plugin artifact '[^']+'\)" "$output_file"; then
    transient_reason="plugin resolution"
  elif [[ "$public_not_found" -eq 1 ]]; then
    transient_reason="repository not-found"
  fi

  job_waited=0
  if [[ -n "$job_wait_file" && -f "$job_wait_file" ]]; then
    job_waited="$(<"$job_wait_file")"
    [[ "$job_waited" =~ ^[0-9]+$ ]] || job_waited=0
  fi
  job_wait_left=$(( job_wait_budget - job_waited ))
  if [[ -n "$transient_reason" && "$attempt" -lt "$max_attempts" && "$job_wait_left" -le 0 && "$delay" -gt 0 ]]; then
    echo "::error::Gradle retry steps in this job already waited ${job_waited}s, the job's ${job_wait_budget}s retry wait budget; not retrying."
  elif [[ -n "$transient_reason" && "$attempt" -lt "$max_attempts" ]]; then
    jitter=$(( RANDOM % (delay / 3 + 1) ))
    sleep_for=$(( delay + jitter ))
    if [[ "$sleep_for" -gt "$job_wait_left" && "$delay" -gt 0 ]]; then
      sleep_for="$job_wait_left"
    fi
    refresh_note=""
    if [[ "$is_dependency_not_found" -eq 1 && "$refresh_attempted" -eq 0 && "$command_string" != *--refresh-dependencies* ]]; then
      refresh_attempted=1
      refresh_next=1
      refresh_note=" with --refresh-dependencies"
    fi
    echo "::warning::Gradle failed with a transient ${transient_reason} failure (attempt ${attempt}/${max_attempts}). Retrying${refresh_note} in ${sleep_for}s..."
    rm -f -- "$output_file"
    output_file=""
    sleep "$sleep_for"
    if [[ -n "$job_wait_file" ]]; then
      printf '%s' "$(( job_waited + sleep_for ))" > "$job_wait_file"
    fi
    attempt=$((attempt + 1))
    delay=$((delay * 3))
    continue
  fi

  if [[ "$genuine_failure" -eq 0 && "$is_dependency_not_found" -eq 1 && "$refresh_attempted" -eq 0 && "$command_string" != *--refresh-dependencies* ]]; then
    refresh_attempted=1
    refresh_next=1
    echo "::warning::Gradle dependency resolution failed; retrying once with --refresh-dependencies."
    rm -f -- "$output_file"
    output_file=""
    continue
  fi

  if [[ -n "$transient_reason" ]]; then
    echo "::error::Gradle transient ${transient_reason} retry budget exhausted after ${attempt}/${max_attempts} attempts."
  fi
  exit "$status"
done
