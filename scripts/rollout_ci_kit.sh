#!/usr/bin/env bash
set -euo pipefail

ORG="${ORG:-quokkify}"
ANSWERS_DIR="${1:?usage: rollout_ci_kit.sh ANSWERS_DIR}"
TOOLKIT_REPO="quokkify/ci-kit"
TOOLKIT_SOURCE="https://github.com/$TOOLKIT_REPO.git"
TOOLKIT_REF="${TOOLKIT_REF:?export an exact reviewed tag, for example TOOLKIT_REF=v2.6.0}"
if [[ ! "$TOOLKIT_REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "TOOLKIT_REF must be an exact SemVer tag such as v2.6.0" >&2
  exit 1
fi
gh release view "$TOOLKIT_REF" --repo "$TOOLKIT_REPO" >/dev/null
BRANCH="chore/ci-kit-${TOOLKIT_REF}"
WORK_ROOT="$(mktemp -d)"
trap 'rm -rf "$WORK_ROOT"' EXIT

canonicalize_answers_source() {
  python3 - "$1" "$TOOLKIT_SOURCE" <<'PY'
from pathlib import Path
import re
import sys

answers = Path(sys.argv[1])
source = sys.argv[2]
if answers.is_symlink():
    raise SystemExit(f"{answers} must not be a symlink")
content = answers.read_text(encoding="utf-8")
updated, substitutions = re.subn(r"^_src_path:.*$", f"_src_path: {source}", content, flags=re.MULTILINE)
if substitutions != 1:
    raise SystemExit(f"{answers} must contain exactly one _src_path entry")
answers.write_text(updated, encoding="utf-8")
PY
}

shopt -s nullglob
answer_files=("$ANSWERS_DIR"/*.yml)
if (( ${#answer_files[@]} == 0 )); then
  echo "No .yml answer files found in $ANSWERS_DIR" >&2
  exit 1
fi

for answers in "${answer_files[@]}"; do
  answers="$(realpath "$answers")"
  repo="$(basename "$answers" .yml)"
  full_repo="$ORG/$repo"
  worktree="$WORK_ROOT/$repo"

  echo "==> $full_repo"
  existing_pr="$(gh pr list --repo "$full_repo" --head "$BRANCH" --state open --json url --jq '.[0].url // empty')"
  if [[ -n "$existing_pr" ]]; then
    echo "    already open: $existing_pr"
    continue
  fi

  gh repo clone "$full_repo" "$worktree" -- --quiet
  default_branch="$(gh repo view "$full_repo" --json defaultBranchRef --jq .defaultBranchRef.name)"
  if git -C "$worktree" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    echo "Remote branch $BRANCH already exists in $full_repo without an open PR; stopping" >&2
    exit 1
  fi
  git -C "$worktree" switch -c "$BRANCH"

  if [[ -f "$worktree/.copier-answers.yml" ]]; then
    if ! grep -Eq "^_src_path:[[:space:]]*['\"]?(gh:quokkify/ci-kit|https://github\.com/quokkify/ci-kit(\.git)?)[\"']?[[:space:]]*$" "$worktree/.copier-answers.yml"; then
      echo "$full_repo is managed by a different Copier template; stopping before changes" >&2
      exit 1
    fi
    copier update "$worktree" \
      --vcs-ref "$TOOLKIT_REF" \
      --data-file "$answers" \
      --data "toolkit_version=$TOOLKIT_REF" \
      --skip README.md \
      --defaults \
      --trust
    canonicalize_answers_source "$worktree/.copier-answers.yml"
  else
    copier copy "$TOOLKIT_SOURCE" "$worktree" \
      --vcs-ref "$TOOLKIT_REF" \
      --data-file "$answers" \
      --data "toolkit_version=$TOOLKIT_REF" \
      --skip README.md \
      --overwrite \
      --defaults \
      --trust
  fi

  if [[ -z "$(git -C "$worktree" status --porcelain)" ]]; then
    echo "    no changes"
    continue
  fi

  git -C "$worktree" add -A
  if git -C "$worktree" diff --cached -U0 | grep -Eq '^\+(<<<<<<<|=======|>>>>>>>)'; then
    echo "Copier conflict in $full_repo; stopping before push" >&2
    exit 1
  fi
  git -C "$worktree" commit -m "chore: apply ci-kit ${TOOLKIT_REF}"
  git -C "$worktree" push -u origin "$BRANCH"
  gh pr create \
    --repo "$full_repo" \
    --base "$default_branch" \
    --head "$BRANCH" \
    --title "chore: apply ci-kit ${TOOLKIT_REF}" \
    --body "Generated with Copier from ${TOOLKIT_REPO}@${TOOLKIT_REF}. Review all generated workflow and Renovate changes before merging."
done
