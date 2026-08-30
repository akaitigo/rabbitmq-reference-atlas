#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CORE_COMMIT=072d7ca77981f51754e824d70c6d4ecd55ea67e5
FILES=("$ROOT/atlas.yaml" "$ROOT/sources.lock.yaml" "$ROOT/coverage.yaml" "$ROOT/skill.package.yaml" "$ROOT/mastery.yaml" "$ROOT/definitive.yaml" "$ROOT/surface.inventory.yaml" "$ROOT/migrations/definitive-v2.yaml" "$ROOT/evidence/history/v0.1.0/core-v2-adapter/completion-certificate.json" "$ROOT/evals/rabbitmq-reference-atlas.definitive-skill-eval.json" "$ROOT/evidence/dependency-graph.json")
while IFS= read -r file; do FILES+=("$file"); done < <(find "$ROOT/evidence" -maxdepth 1 -type f \( -name '*.evidence.json' -o -name '*.evidence.yaml' \) | sort)
while IFS= read -r file; do FILES+=("$file"); done < <(find "$ROOT/surface/authority" -maxdepth 1 -type f -name '*.authority-surfaces.yaml' | sort)
while IFS= read -r file; do FILES+=("$file"); done < <(find "$ROOT/claims" -maxdepth 1 -type f -name '*.claim.yaml' | sort)

if [[ -n "${ATLAS_BIN:-}" ]]; then
  "$ATLAS_BIN" validate "${FILES[@]}"
  "$ATLAS_BIN" audit "$ROOT"
elif [[ -d "$ROOT/../reference-atlas-core/.git" ]] && git -C "$ROOT/../reference-atlas-core" cat-file -e "$CORE_COMMIT^{commit}"; then
  CORE_SNAPSHOT=$(mktemp -d "${TMPDIR:-/tmp}/reference-atlas-core.XXXXXX")
  trap 'rm -rf "$CORE_SNAPSHOT"' EXIT
  git -C "$ROOT/../reference-atlas-core" archive "$CORE_COMMIT" | tar -x -C "$CORE_SNAPSHOT"
  (cd "$CORE_SNAPSHOT" && go run ./cmd/atlas validate "${FILES[@]}" && go run ./cmd/atlas audit "$ROOT" --gate evidence-dependency && go run ./cmd/atlas audit "$ROOT")
else
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" validate "${FILES[@]}"
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" audit "$ROOT" --gate evidence-dependency
  go run "github.com/akaitigo/reference-atlas-core/cmd/atlas@$CORE_COMMIT" audit "$ROOT"
fi
