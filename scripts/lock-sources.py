#!/usr/bin/env python3
import argparse
import hashlib
import pathlib
import urllib.request

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="一次資料の内容Digestを更新する")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    path = ROOT / "sources.lock.yaml"
    document = yaml.safe_load(path.read_text())
    for source in document["sources"]:
        if source["kind"] == "runtime-inventory":
            continue
        request = urllib.request.Request(source["url"], headers={"User-Agent": "rabbitmq-reference-atlas/0.1"})
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            content = response.read()
        source["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
        print(f"locked {source['id']}: {source['digest']}")
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()

