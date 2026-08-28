.PHONY: test skill-eval atlas-validate repo-validate legal check labs tls-lab observability-lab upgrade-lab lock-sources completion

export GOCACHE := $(CURDIR)/.cache/go-build
export GOMODCACHE := $(CURDIR)/.cache/go-mod

test:
	go test ./...

skill-eval:
	python3 scripts/run-skill-evals.py

atlas-validate:
	bash scripts/atlas-validate.sh

repo-validate:
	python3 scripts/validate-repository.py

legal:
	test -s LICENSE
	test -s NOTICE
	test -s third_party/manifest.yaml
	test -s sbom.spdx.json
	test -s third_party/sbom.cdx.json

check: test skill-eval atlas-validate repo-validate legal

labs:
	bash scripts/run-labs.sh

tls-lab:
	bash scripts/run-tls-lab.sh

observability-lab:
	bash scripts/run-observability-lab.sh

upgrade-lab:
	bash scripts/run-upgrade-migration-lab.sh

lock-sources:
	python3 scripts/lock-sources.py
	python3 scripts/sync-authority-digest.py

completion:
	python3 scripts/generate-completion-certificate.py
	python3 scripts/validate-repository.py --release
	bash scripts/atlas-validate.sh
