.PHONY: test skill-eval definitive-skill-eval scenario-proof atlas-validate repo-validate authority-locators authority-body authority-review non-regression parity neutral-language legal check labs amqp10-lab plugin-protocol-lab tls-lab observability-lab upgrade-lab lock-sources completion

export GOCACHE := $(CURDIR)/.cache/go-build
export GOMODCACHE := $(CURDIR)/.cache/go-mod

test:
	go test ./...

skill-eval:
	python3 scripts/generate-skill-mastery-contract.py
	python3 scripts/evaluate-forward-agent.py
	python3 scripts/run-skill-evals.py

definitive-skill-eval:
	python3 scripts/test-definitive-skill-eval.py

scenario-proof:
	python3 scripts/test-scenario-proofs.py
	python3 scripts/validate-scenario-proofs.py

atlas-validate:
	bash scripts/atlas-validate.sh

repo-validate:
	python3 scripts/validate-repository.py

authority-locators:
	python3 scripts/validate-authority-locators.py

authority-body:
	python3 scripts/test-authority-body-inventory.py
	python3 scripts/validate-authority-body-inventory.py

authority-review:
	python3 scripts/test-authority-review-queue.py
	python3 scripts/validate-authority-review-queue.py

non-regression:
	python3 scripts/validate-non-regression.py

parity:
	python3 scripts/generate-rabbitmq-depth-parity.py --check

neutral-language:
	python3 scripts/validate-neutral-language.py

legal:
	test -s LICENSE
	test -s NOTICE
	test -s third_party/manifest.yaml
	test -s sbom.spdx.json
	test -s third_party/sbom.cdx.json

check: test skill-eval definitive-skill-eval scenario-proof atlas-validate repo-validate authority-locators authority-body authority-review non-regression parity neutral-language legal

labs:
	bash scripts/run-labs.sh

amqp10-lab:
	go run ./cmd/rmq-amqp10-handshake --endpoints 127.0.0.1:25672,127.0.0.1:25673,127.0.0.1:25674 --output-dir evidence/raw
	python3 scripts/generate-amqp10-evidence.py

plugin-protocol-lab:
	bash scripts/run-plugin-protocol-lab.sh

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
