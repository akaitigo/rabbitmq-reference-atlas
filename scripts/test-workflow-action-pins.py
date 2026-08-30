#!/usr/bin/env python3
"""GitHub Actionsのmutable refを拒否し、承認済みcommit pinを固定する。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
EXPECTED = {
    "checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "setup-go": "40f1582b2485089dde7abd97c1529aa768e1baff",
}
ACTION = re.compile(r"\buses:\s*actions/(checkout|setup-go)@([^\s#]+)")


class WorkflowPinError(ValueError):
    pass


def validate(documents: dict[str, str]) -> None:
    found = {name: 0 for name in EXPECTED}
    for path, source in documents.items():
        for name, reference in ACTION.findall(source):
            found[name] += 1
            if reference != EXPECTED[name]:
                raise WorkflowPinError(f"mutable-or-unapproved-action-ref:{path}:actions/{name}@{reference}")
    missing = [name for name, count in found.items() if count == 0]
    if missing:
        raise WorkflowPinError("required-action-pin-missing:" + ",".join(missing))


documents = {
    path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOWS.glob("*.y*ml"))
}
validate(documents)

migration = yaml.safe_load((ROOT / "migrations/public-main-baseline-v2.yaml").read_text(encoding="utf-8"))
pin_mappings = {
    row["old_value"]: row["new_value"]
    for row in migration["replacements"]
    if row.get("category") == "ci-action-pin"
}
assert pin_mappings == {
    "actions/checkout@v4": f"actions/checkout@{EXPECTED['checkout']}",
    "actions/setup-go@v5": f"actions/setup-go@{EXPECTED['setup-go']}",
}

for action, mutable in (("checkout", "v4"), ("setup-go", "v5")):
    mutated = dict(documents)
    target = next(path for path, source in mutated.items() if f"actions/{action}@" in source)
    mutated[target] = mutated[target].replace(
        f"actions/{action}@{EXPECTED[action]}", f"actions/{action}@{mutable}", 1
    )
    try:
        validate(mutated)
    except WorkflowPinError as error:
        assert str(error).startswith("mutable-or-unapproved-action-ref:"), error
    else:
        raise WorkflowPinError(f"mutable fixture accepted: actions/{action}@{mutable}")

print("workflow action pin contract PASS: checkout/setup-go exact commit、mutable tag negative fixtureを確認")
