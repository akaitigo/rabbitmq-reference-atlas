#!/usr/bin/env python3
from copy import deepcopy
import json
import tempfile
from pathlib import Path

import scenario_proof
from scenario_proof import build, dedicated_runtime_proof, validate_built

bundle, proofs = build()
assert not validate_built(bundle, proofs)

# 専用実行reportが全条件を満たす場合だけScenario gapが閉じることを確認する。
closure_contract = scenario_proof.load_yaml(scenario_proof.ROOT / "scenario-closure.yaml")
original_root = scenario_proof.ROOT
with tempfile.TemporaryDirectory() as tmp:
    scenario_proof.ROOT = Path(tmp)
    source_path = scenario_proof.ROOT / "client/source.go"
    harness_path = scenario_proof.ROOT / "harness/run.go"
    source_path.parent.mkdir(parents=True)
    harness_path.parent.mkdir(parents=True)
    source_path.write_text("package client\n", encoding="utf-8")
    harness_path.write_text("package harness\n", encoding="utf-8")
    source = {"path": "client/source.go", "digest": scenario_proof.sha_file(source_path)}
    harness = {"path": "harness/run.go", "digest": scenario_proof.sha_file(harness_path)}
    variants = []
    for variant_id in closure_contract["profile_variants"]["broker-cluster-3"]:
        channels = {}
        for channel in scenario_proof.CHANNELS:
            artifact = scenario_proof.ROOT / f"evidence/scenario-runtime/artifacts/example.behavior/normal/{variant_id}/{channel}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps({"variant": variant_id, "channel": channel}), encoding="utf-8")
            channels[channel] = {
                "path": artifact.relative_to(scenario_proof.ROOT).as_posix(), "digest": scenario_proof.sha_file(artifact),
                "size_bytes": artifact.stat().st_size, "channel": channel, "media_type": "application/json",
            }
        variants.append({
            "id": variant_id, "attempts": 1, "retries": 0,
            "broker": {"runtime_kind": "actual-broker", "product": "RabbitMQ", "version": "4.3.5",
                       "image_digest": closure_contract["identity"]["broker_image_digest"]},
            "client": {"runtime_kind": "actual-client", "name": "fixture-client", "version": "1.0.0",
                       "source_digest": source["digest"]},
            "runtime": {"profile": "broker-cluster-3", "platform": "fixture-platform", "execution_id": "fixture-run"},
            "oracle": {"id": f"oracle.{variant_id}", "assertions": ["expected outcome"], "passed": True},
            "source": source, "harness": harness, "artifact_channels": channels,
        })
    report_path = scenario_proof.ROOT / "evidence/scenario-runtime/example.behavior/normal.runtime.json"
    report_path.parent.mkdir(parents=True)
    report = {
        "behavior_id": "example.behavior", "authority_surface_id": "surface.example", "scenario": "normal",
        "runtime_profile": "broker-cluster-3", "status": "passed", "attempts": 1, "retries": 0,
        "source": source, "harness": harness, "variants": variants,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    dedicated, gaps, _ = dedicated_runtime_proof(
        {"behavior_id": "example.behavior", "scenario": "normal", "profile": "broker-cluster-3", "applicability": "required"},
        {"authority_surface_id": "surface.example", "surface_ids": []}, closure_contract)
    assert dedicated["scenario_gap_closed"] and not gaps
    report["retries"] = 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    dedicated, gaps, _ = dedicated_runtime_proof(
        {"behavior_id": "example.behavior", "scenario": "normal", "profile": "broker-cluster-3", "applicability": "required"},
        {"authority_surface_id": "surface.example", "surface_ids": []}, closure_contract)
    assert not dedicated["scenario_gap_closed"] and "execution.retries-not-zero" in gaps
scenario_proof.ROOT = original_root

path = next(path for path, proof in proofs.items() if proof["applicability"] == "required")

mutated = deepcopy(proofs)
mutated[path]["integrated_reference"]["behavior_completion_reuse_allowed"] = True
assert any("統合Proof" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["dedicated_runtime"]["variant_proofs"][0]["artifact_channels"]["packet"] = {"artifact": None, "gap_ids": []}
assert any("専用Artifact channel" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["closure"]["scenario_gap_closed"] = True
mutated[path]["closure"]["attempts_one_retry_zero"] = False
assert any("Scenario gap Closure" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["legacy_observation"]["counts_toward_scenario_gap_closure"] = True
assert any("Legacy observation" in error for error in validate_built(bundle, mutated))

mutated = deepcopy(proofs)
mutated[path]["closure"]["completion_eligible"] = True
assert any("Authority atomic binding" in error or "必須binding" in error for error in validate_built(bundle, mutated))

mutated_bundle = deepcopy(bundle)
mutated_bundle["duplicates"] = {"aggregate": ["a:normal", "b:normal"]}
assert any("複数row" in error for error in validate_built(mutated_bundle, proofs))

mutated_bundle = deepcopy(bundle)
mutated_bundle["duplicate_artifacts"] = {"evidence/scenario-runtime/artifacts/shared.log": ["a:normal", "b:normal"]}
assert any("専用Artifact" in error for error in validate_built(mutated_bundle, proofs))

print("Scenario Proof negative test通過: 統合/Legacy流用、retry迂回、channel欠落、Authority迂回、Artifact重複を拒否")
