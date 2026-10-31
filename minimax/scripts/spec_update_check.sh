#!/usr/bin/env bash
# Demonstrate that changing a live requirement refreshes persisted retrieval, not a constant response.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${MM_PYTHON:-python3}"
spec='.sdd/specs/reading-list-r2.json'
backup="$(mktemp)"
cp "$spec" "$backup"
trap 'cp "$backup" "$spec"; rm -f "$backup"' EXIT
"$PYTHON" -m ctxsdd init >/dev/null
before="$("$PYTHON" -m ctxsdd plan --task 'status min_rating')"
"$PYTHON" - <<'PY'
import json
from pathlib import Path
p=Path('.sdd/specs/reading-list-r2.json')
s=json.loads(p.read_text(encoding='utf-8'))
s['requirements'].append('TEST SPEC UPDATE: price_bucket must be presented as an additional requirement')
p.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
PY
after="$("$PYTHON" -m ctxsdd plan --task 'status min_rating price_bucket')"
BEFORE="$before" AFTER="$after" "$PYTHON" - <<'PY'
import json,os
b=json.loads(os.environ['BEFORE']);a=json.loads(os.environ['AFTER'])
assert b['backend']==a['backend']
assert b['specifications'][0]['sha256'] != a['specifications'][0]['sha256']
assert 'price_bucket' in str(a['specifications'])
assert 'price_bucket' in str(a['evidence'])
print('PASS: changed specification re-imported, SHA changed and database-backed context updated')
PY
