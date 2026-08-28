#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CORE_COMMIT=6793642472d4011786c35b98fdb60cd4212e9699
FILES=("$ROOT/atlas.yaml" "$ROOT/sources.lock.yaml" "$ROOT/coverage.yaml" "$ROOT/skill.package.yaml" "$ROOT/mastery.yaml")
while IFS= read -r file; do FILES+=("$file"); done < <(find "$ROOT/evidence" -maxdepth 1 -type f \( -name '*.evidence.json' -o -name '*.evidence.yaml' \) | sort)

if [[ -n "${ATLAS_BIN:-}" ]]; then
  "$ATLAS_BIN" validate "${FILES[@]}"
  "$ATLAS_BIN" audit "$ROOT"
elif [[ -d "$ROOT/../reference-atlas-core/.git" ]] && git -C "$ROOT/../reference-atlas-core" cat-file -e "$CORE_COMMIT^{commit}"; then
  CORE_SNAPSHOT=$(mktemp -d "${TMPDIR:-/tmp}/reference-atlas-core.XXXXXX")
  trap 'rm -rf "$CORE_SNAPSHOT"' EXIT
  git -C "$ROOT/../reference-atlas-core" archive "$CORE_COMMIT" | tar -x -C "$CORE_SNAPSHOT"
  (cd "$CORE_SNAPSHOT" && go run ./cmd/atlas validate "${FILES[@]}" && go run ./cmd/atlas audit "$ROOT")
else
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" validate "${FILES[@]}"
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" audit "$ROOT"
fi
