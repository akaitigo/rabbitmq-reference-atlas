#!/usr/bin/env python3
import hashlib
import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    source_path = ROOT / "sources.lock.yaml"
    coverage_path = ROOT / "coverage.yaml"
    digest = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    coverage = yaml.safe_load(coverage_path.read_text())
    coverage["authority_lock_digest"] = digest
    coverage_path.write_text(yaml.safe_dump(coverage, allow_unicode=True, sort_keys=False))
    print(digest)


if __name__ == "__main__":
    main()

