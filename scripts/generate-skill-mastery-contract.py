#!/usr/bin/env python3
import json

from skill_routing import OUTCOME_EXECUTION, ROOT, STOP_CONDITIONS, sha_file

import yaml


def main() -> int:
    mastery = yaml.safe_load((ROOT / "mastery.yaml").read_text())
    contract = {
        "schema_version": 1,
        "atlas_id": mastery["atlas_id"],
        "epoch": mastery["epoch"],
        "source": {"path": "mastery.yaml", "digest": sha_file(ROOT / "mastery.yaml")},
        "outcomes": [{**item, "execution_contract": OUTCOME_EXECUTION[item["id"]]} for item in mastery["outcomes"]],
        "surfaces": mastery["surfaces"],
        "stop_conditions": STOP_CONDITIONS,
        "completion_policy": {
            "matrix_contract_pass_is_sufficient": False,
            "requires_target_variant_authority_broker_protocol_evidence": True,
            "requires_all_required_target_states_covered": True,
            "requires_independent_agent_forward_eval": True,
        },
    }
    output = ROOT / ".agents/skills/rabbitmq-reference-router/references/mastery-contract.json"
    output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
    print(f"Skill Mastery contract generated: outcomes={len(contract['outcomes'])} surfaces={len(contract['surfaces'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
