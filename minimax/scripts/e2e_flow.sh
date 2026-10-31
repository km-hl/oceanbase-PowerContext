#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${MM_PYTHON:-python3}"
source_file='business/reading_list/service.py'
backup="$(mktemp)"
cp "$source_file" "$backup"
restore() { cp "$backup" "$source_file"; rm -f "$backup"; }
trap restore EXIT
cp docs/snapshots/before/service.py "$source_file"
if "$PYTHON" -m unittest discover -s business/tests -p 'test_feature.py' -v > /tmp/mm-sdd-before-feature.log 2>&1; then
  echo 'ERROR: feature tests passed before implementation' >&2; exit 1
fi
"$PYTHON" -m unittest discover -s business/tests -p 'test_regression.py' -v > /tmp/mm-sdd-before-regression.log 2>&1
cp "$backup" "$source_file"
"$PYTHON" -m unittest discover -s business/tests -p 'test_*.py' -v
"$PYTHON" -m ctxsdd init
"$PYTHON" -m ctxsdd plan --task '阅读清单 status min_rating 筛选' > /tmp/mm-sdd-context.json
"$PYTHON" -m ctxsdd verify > /tmp/mm-sdd-verification.json
"$PYTHON" -m ctxsdd state > /tmp/mm-sdd-state.json
"$PYTHON" - <<'PY'
import json
from pathlib import Path
assert json.loads(Path('/tmp/mm-sdd-verification.json').read_text())['passed']
assert json.loads(Path('/tmp/mm-sdd-state.json').read_text())['latest_verification']['fresh']
print('SDD OFFLINE E2E PASS: old feature failed, old regression passed, new 9 tests passed, state persisted')
PY
