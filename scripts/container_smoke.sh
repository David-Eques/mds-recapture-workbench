#!/bin/sh
set -eu

image="${1:-mds-recapture-workbench}"
smoke_dir="$(mktemp -d)"
container_id=""
cleanup() {
  if [ -n "$container_id" ]; then
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
  rm -rf "$smoke_dir"
}
trap cleanup EXIT INT TERM

uv run python -m tests.public.generate_e2e_case "$smoke_dir"
container_id="$(docker run -d -p 8765:8000 "$image")"

attempt=0
until curl --fail --silent http://127.0.0.1:8765/readyz >"$smoke_dir/ready.json"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    docker logs "$container_id"
    exit 1
  fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:8765/api/capabilities \
  | python -c 'import json,sys; data=json.load(sys.stdin); assert data["ready"] is True; assert data["runtime"]["cms_grouper"]["version"] == "2.4000"'

curl --fail --silent --show-error \
  -F "assessment=@$smoke_dir/assessment.json;type=application/json" \
  -F "documents=@$smoke_dir/unique-scanned-note.pdf;type=application/pdf" \
  -F 'document_metadata=[{"filename":"unique-scanned-note.pdf","document_date":"2026-05-20","document_type":"dietary"}]' \
  http://127.0.0.1:8765/api/analyze >"$smoke_dir/analysis.json"

python - "$smoke_dir/analysis.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
finding = next(item for item in data["findings"] if item["support_tier"] == "text_review_candidate")
counterfactual = finding["counterfactual"]
assert counterfactual["as_coded"]["hipps"] == "KAXE1"
assert counterfactual["as_supported"]["hipps"] == "KBXE1"
assert counterfactual["potential_per_day_delta"] == "30.54"
assert counterfactual["counted_per_day_delta"] is None
evidence = finding["evidence"][0]
assert evidence["verification_basis"] == "ocr_text_exact_substring"
assert evidence["page"] == 1 and evidence["bbox"] is not None
PY
