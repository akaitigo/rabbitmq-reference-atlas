#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-${repo_root}/evidence/raw/observability-state.json}"
task_tmp="$(mktemp -d "${TMPDIR:-/tmp}/rabbitmq-observability.XXXXXX")"
trap 'rm -rf "${task_tmp}"' EXIT

state_json="${task_tmp}/state.json"
nodes_jsonl="${task_tmp}/nodes.jsonl"
: >"${nodes_jsonl}"

cd "${repo_root}"
go run ./cmd/rmq-observability -output "${state_json}"

services=(rabbitmq-1 rabbitmq-2 rabbitmq-3)
for service in "${services[@]}"; do
  metrics="${task_tmp}/${service}.prom"
  docker compose -f environments/compose.yaml exec -T "${service}" \
    erl -noshell -eval 'inets:start(), {ok, {{_, 200, _}, _, Body}} = httpc:request("http://127.0.0.1:15692/metrics"), io:format("~s", [Body]), halt().' \
    >"${metrics}"

  test -s "${metrics}"
  grep -q '^# TYPE rabbitmq_' "${metrics}"
  grep -q '^rabbitmq_alarms_free_disk_space_watermark ' "${metrics}"
  grep -q '^rabbitmq_alarms_memory_used_watermark ' "${metrics}"
  grep -q '^rabbitmq_unreachable_cluster_peers_count ' "${metrics}"

  digest="$(shasum -a 256 "${metrics}" | awk '{print $1}')"
  bytes="$(wc -c <"${metrics}" | tr -d ' ')"
  metric_families="$(grep -c '^# TYPE ' "${metrics}")"
  rabbitmq_metric_families="$(grep -c '^# TYPE rabbitmq_' "${metrics}")"

  jq -n \
    --arg node "${service}" \
    --arg endpoint "http://127.0.0.1:15692/metrics" \
    --arg sha256 "${digest}" \
    --argjson bytes "${bytes}" \
    --argjson metric_families "${metric_families}" \
    --argjson rabbitmq_metric_families "${rabbitmq_metric_families}" \
    --rawfile metrics "${metrics}" \
    '{
      node: $node,
      endpoint: $endpoint,
      transport: "container-local-erlang-httpc",
      bytes: $bytes,
      sha256: $sha256,
      metric_families: $metric_families,
      rabbitmq_metric_families: $rabbitmq_metric_families,
      selected_samples: (
        $metrics | split("\n") | map(select(test("^rabbitmq_(alarms_free_disk_space_watermark|alarms_memory_used_watermark|unreachable_cluster_peers_count) ")))
      )
    }' >>"${nodes_jsonl}"
done

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
final_json="${task_tmp}/observability-state.json"
jq -n \
  --arg created_at "${created_at}" \
  --slurpfile state "${state_json}" \
  --slurpfile nodes "${nodes_jsonl}" \
  '{
    schema_version: 1,
    evidence_id: "observability-state",
    created_at: $created_at,
    rabbitmq_version: $state[0].rabbitmq_version,
    run_id: $state[0].run_id,
    state_transition: $state[0],
    prometheus: {
      endpoint_port: 15692,
      host_port_published: false,
      nodes: $nodes
    },
    checks: (
      $state[0].checks +
      ($nodes | map({
        name: ("observability.prometheus." + .node),
        passed: ((.bytes > 0) and (.metric_families > 0) and (.rabbitmq_metric_families > 0) and (.selected_samples | length == 3)),
        observed: {
          node: .node,
          bytes: .bytes,
          sha256: .sha256,
          metric_families: .metric_families,
          rabbitmq_metric_families: .rabbitmq_metric_families,
          selected_samples: .selected_samples
        }
      }))
    ),
    passed: (
      $state[0].passed and
      ($nodes | length == 3) and
      ($nodes | all((.bytes > 0) and (.metric_families > 0) and (.rabbitmq_metric_families > 0) and (.selected_samples | length == 3)))
    )
  }' >"${final_json}"

mkdir -p "$(dirname "${output}")"
install -m 0644 "${final_json}" "${output}"
jq '{evidence_id, rabbitmq_version, run_id, passed, transition_checks: [.state_transition.checks[] | {name, passed}], prometheus_nodes: [.prometheus.nodes[] | {node, bytes, sha256, metric_families, rabbitmq_metric_families, selected_samples}]}' "${output}"

test "$(jq -r '.passed' "${output}")" = "true"
