#!/usr/bin/env bash
# Quick end-to-end check of the phase 1 instrument. Not a measurement run:
# use bench/validate_harness.py for that.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY=.venv/bin/python
PORT=${PORT:-8099}

$PY -c "import ast
for f in ['bench/mock_server.py','bench/harness.py','bench/validate_harness.py']:
    ast.parse(open(f).read()); print('syntax ok', f)"

$PY bench/mock_server.py --profile mid --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

# The venv sits on a DrvFS mount, so importing aiohttp can take seconds.
# Poll for readiness rather than guessing a sleep duration.
for _ in $(seq 1 100); do
  if curl -sf -m 2 "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  sleep 0.5
done

echo '--- health ---'
curl -s "http://127.0.0.1:$PORT/health"; echo
echo '--- streaming: first event, then final event ---'
curl -sN -X POST "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  | grep '^data:' | sed -n '1p;$p'
echo '--- streaming: measured wall time (configured total 410 ms) ---'
curl -sN -o /dev/null -w 'total %{time_total}s\n' -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true}'
echo '--- non-streaming ---'
curl -s -o /dev/null -w 'total %{time_total}s\n' -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":false}'
echo '--- injected failure (expect http_402 rows) ---'
curl -s -o /dev/null -w 'status %{http_code}\n' -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' -H 'X-Mock-Force-Status: 402' \
  -d '{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true}'
echo '--- short harness run, concurrency 2 ---'
$PY bench/harness.py --url "http://127.0.0.1:$PORT" --concurrency 2 --expected-latency-ms 410 \
  --duration 6 --warmup 2 --config-id smoke --out results/raw/smoke/smoke.csv
echo '--- csv head ---'
head -3 results/raw/smoke/smoke.csv
echo '--- error-path run: every request forced to fail with 402 ---'
$PY bench/harness.py --url "http://127.0.0.1:$PORT" --concurrency 1 --expected-latency-ms 410 \
  --duration 4 --warmup 0 --config-id smoke-errors --mock-force-status 402 \
  --out results/raw/smoke/smoke-errors.csv > /dev/null
$PY - <<'PYEOF'
import csv
rows = list(csv.DictReader(open("results/raw/smoke/smoke-errors.csv")))
statuses = {r["status"] for r in rows}
errors = {r["error_type"] for r in rows}
assert rows, "no rows recorded on the error path"
assert statuses == {"error"}, statuses
assert errors == {"http_402"}, errors
# A failed request must still carry both measurement points and no invented
# output figures.
for r in rows:
    assert r["t1_total_ms"] and r["t2_model_ms"], r
    assert r["ttft_ms"] == "" and r["tokens_per_sec"] == "", r
    assert r["output_tokens"] == "0", r
print("error path ok: %d rows, status=error, error_type=http_402, "
      "t1/t2 present, no invented token figures" % len(rows))
PYEOF
