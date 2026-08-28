#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CORE_COMMIT=1c85bed8d45a3daee3e5cda7fbbe144607ac1259
FILES=("$ROOT/atlas.yaml" "$ROOT/sources.lock.yaml" "$ROOT/coverage.yaml" "$ROOT/skill.package.yaml")
while IFS= read -r file; do FILES+=("$file"); done < <(find "$ROOT/evidence" -maxdepth 1 -type f \( -name '*.evidence.json' -o -name '*.evidence.yaml' \) | sort)

if [[ -n "${ATLAS_BIN:-}" ]]; then
  "$ATLAS_BIN" validate "${FILES[@]}"
elif [[ -d "$ROOT/../reference-atlas-core/.git" ]] && git -C "$ROOT/../reference-atlas-core" cat-file -e "$CORE_COMMIT^{commit}"; then
  CORE_SNAPSHOT=$(mktemp -d "${TMPDIR:-/tmp}/reference-atlas-core.XXXXXX")
  trap 'rm -rf "$CORE_SNAPSHOT"' EXIT
  git -C "$ROOT/../reference-atlas-core" archive "$CORE_COMMIT" | tar -x -C "$CORE_SNAPSHOT"
  (cd "$CORE_SNAPSHOT" && go run ./cmd/atlas validate "${FILES[@]}")
else
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" validate "${FILES[@]}"
fi
