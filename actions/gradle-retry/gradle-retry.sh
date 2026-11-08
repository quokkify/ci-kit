#!/usr/bin/env bash
# Run a Gradle command with bounded recovery for transient resolution failures.
# Repository HTTP 403/429 failures retain exponential retry; a dependency
# not-found gets one refresh.
set -uo pipefail

command_text() {
  local value="${1//"%"/%25}"
  value="${value//$'\r'/%0D}"
  printf '%s' "${value//$'\n'/%0A}"
}

command_string="${GRADLE_RETRY_COMMAND:-}"
max_attempts="${GRADLE_RETRY_MAX_ATTEMPTS:-3}"
delay="${GRADLE_RETRY_INITIAL_DELAY_SECONDS:-30}"

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

attempt=1
refresh_attempted=0
output_file=""
cleanup() {
  if [[ -n "$output_file" ]]; then
    rm -f -- "$output_file"
  fi
}
trap cleanup EXIT INT TERM

while true; do
  output_file="$(mktemp "${TMPDIR:-/tmp}/gradle-retry.XXXXXX")"
  bash -euo pipefail -c "$command_string" 2>&1 | tee "$output_file"
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

  if [[ -n "$transient_status" && "$attempt" -lt "$max_attempts" ]]; then
    jitter=$(( RANDOM % (delay / 3 + 1) ))
    sleep_for=$(( delay + jitter ))
    echo "::warning::Gradle failed with a transient repository HTTP ${transient_status} (attempt ${attempt}/${max_attempts}). Retrying in ${sleep_for}s..."
    rm -f -- "$output_file"
    output_file=""
    sleep "$sleep_for"
    attempt=$((attempt + 1))
    delay=$((delay * 3))
    continue
  fi

  if [[ -n "$transient_status" ]]; then
    echo "::error::Gradle transient repository HTTP ${transient_status} retry budget exhausted after ${attempt}/${max_attempts} attempts."
  fi

  # Gradle's dependency-resolution not-found report includes both the module
  # coordinates and a repository search section. Do not treat unrelated DSL
  # errors such as "Could not find method implementation()" as resolvable.
  is_dependency_not_found=0
  if grep -qE 'Could not find [^[:space:]]+:[^[:space:]]+:[^[:space:]]+' "$output_file" \
    && grep -qE 'Searched in( the following locations)?:' "$output_file"; then
    is_dependency_not_found=1
  fi
  if [[ "$is_dependency_not_found" -eq 1 && "$refresh_attempted" -eq 0 && "$command_string" != *--refresh-dependencies* ]]; then
    refresh_attempted=1
    command_string="$command_string --refresh-dependencies"
    echo "::warning::Gradle dependency resolution failed; retrying once with --refresh-dependencies."
    rm -f -- "$output_file"
    output_file=""
    continue
  fi

  exit "$status"
done
