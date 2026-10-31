#!/usr/bin/env bash
# REQUIRED credentialed demonstration. Fails closed; never emits synthetic AI/DB receipts.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${MM_PYTHON:-python3}"
: "${MM_MINIMAX_API_KEY:?Set the actual MiniMax API key in the environment}"
: "${MM_OB_HOST:?Set real OceanBase/seekdb host in the environment}"
export MM_PC_ENABLED=1
session="records/online-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$session"
backup="$(mktemp)"
cp business/reading_list/service.py "$backup"
cleanup() { rc=$?; if [[ $rc -ne 0 ]]; then cp "$backup" business/reading_list/service.py; fi; rm -f "$backup"; }
trap cleanup EXIT
cp docs/snapshots/before/service.py business/reading_list/service.py
sha256sum business/reading_list/service.py > "$session/before-source-sha256.txt"
if "$PYTHON" -m unittest discover -s business/tests -p test_feature.py -v > "$session/before-feature.log" 2>&1; then
  echo 'Pre-change target tests unexpectedly passed' >&2; exit 1
fi
# An import error must not count as a legitimate feature failure.
if grep -qE '^ImportError:|^ModuleNotFoundError:|No module named|FAILED \(errors=[0-9]+\).*ModuleNotFound' "$session/before-feature.log"; then
  echo 'Baseline has environmental/import errors rather than feature failures' >&2; exit 1
fi
"$PYTHON" -m unittest discover -s business/tests -p test_regression.py -v > "$session/before-regression.log" 2>&1
"$PYTHON" -m ctxsdd init > "$session/oceanbase-import.json"
"$PYTHON" -m ctxsdd plan > "$session/context-before.json"
"$PYTHON" -m ctxsdd ask > "$session/minimax-response.json"
"$PYTHON" -m ctxsdd apply --patch records/model.patch > "$session/patch-applied.json"
sha256sum business/reading_list/service.py > "$session/after-source-sha256.txt"
"$PYTHON" -m ctxsdd verify > "$session/verification.json"
"$PYTHON" -m ctxsdd pc-state > "$session/powercontext-restarted.json"
"$PYTHON" -m ctxsdd plan > "$session/context-resumed.json"
"$PYTHON" - <<'PY'
import json
from pathlib import Path
from ctxsdd.core import ROOT
runs = sorted((ROOT / 'records').glob('online-*'))
folder = runs[-1]
start = json.loads((folder/'context-before.json').read_text())
end = json.loads((folder/'context-resumed.json').read_text())
verified = json.loads((folder/'verification.json').read_text())
pc = json.loads((folder/'powercontext-restarted.json').read_text())
assert start['backend'] == 'oceanbase-seekdb'
assert start['indexed_chunks'] > 0
assert any(item['kind']=='spec' for item in start['evidence'])
assert any(item['kind']=='code' for item in start['evidence'])
assert any(item['kind']=='test' for item in start['evidence'])
assert verified['passed']
assert pc['provider'] == 'powercontext'
assert pc['state']['last_verification']['fresh']
assert end['progress']['last_verification']['fresh']
print('REAL E2E PASS:', folder)
PY
printf '%s\n' 'Review/redact records/online-*/ before committing evidence to docs/runs/.'
