#!/usr/bin/env python3
from copy import deepcopy

from scenario_proof import build, validate_built

bundle, proofs = build()
assert not validate_built(bundle, proofs)

path = next(path for path, proof in proofs.items() if proof["applicability"] == "required")

mutated = deepcopy(proofs)
mutated[path]["integrated_reference"]["behavior_completion_reuse_allowed"] = True
assert any("統合Proof" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["artifact_channels"]["packet"] = {"artifacts": [], "gap_ids": []}
assert any("Artifact channel" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["closure"]["completion_eligible"] = True
assert any("Authority atomic binding" in error or "必須binding" in error for error in validate_built(bundle, mutated))

mutated_bundle = deepcopy(bundle)
mutated_bundle["duplicates"] = {"aggregate": ["a:normal", "b:normal"]}
assert any("複数row" in error for error in validate_built(mutated_bundle, proofs))

print("Scenario Proof negative test通過: reuse、channel欠落、Authority迂回、Evidence重複を拒否")
