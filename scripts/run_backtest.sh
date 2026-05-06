#!/usr/bin/env bash
# run_backtest.sh - 자동화된 백테스트 실행
#
# Usage:
#   ./scripts/run_backtest.sh
#   ./scripts/run_backtest.sh --days 60
#   ./scripts/run_backtest.sh --pairs BTC/USDT:USDT,ETH/USDT:USDT --days 30
#
# Env (override defaults):
#   FT_BACKTEST_DAYS, FT_BACKTEST_PAIRS, FT_BACKTEST_STRATEGY,
#   FT_BACKTEST_FREQAI_MODEL, FT_BACKTEST_TIMEFRAME
#
# Output:
#   user_data/backtest_results/<UTC-ts>/{result.json, REPORT.md}
#
# Exit codes:
#   0  - backtest done & gate PASS
#   1  - backtest done but gate FAIL (still useful to inspect)
#   2+ - hard failure (data download / backtest / report)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

DAYS="${FT_BACKTEST_DAYS:-90}"
PAIRS="${FT_BACKTEST_PAIRS:-}"
STRATEGY="${FT_BACKTEST_STRATEGY:-KaiBaseStrategy}"
FREQAI_MODEL="${FT_BACKTEST_FREQAI_MODEL:-LLMEnhancedModel}"
TIMEFRAME="${FT_BACKTEST_TIMEFRAME:-5m}"
SKIP_DOWNLOAD="${FT_BACKTEST_SKIP_DOWNLOAD:-0}"

usage() {
  cat <<EOF
Usage: $0 [options]
  --days N             Lookback days (default: 90)
  --pairs A,B,C        Comma-separated pairs (default: config.json whitelist)
  --strategy NAME      Strategy class (default: KaiBaseStrategy)
  --freqaimodel NAME   FreqAI model (default: LLMEnhancedModel)
  --timeframe TF       Main timeframe (default: 5m)
  --skip-download      Reuse existing OHLCV data
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2;;
    --pairs) PAIRS="$2"; shift 2;;
    --strategy) STRATEGY="$2"; shift 2;;
    --freqaimodel) FREQAI_MODEL="$2"; shift 2;;
    --timeframe) TIMEFRAME="$2"; shift 2;;
    --skip-download) SKIP_DOWNLOAD=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

# Pairs from config.json if not supplied
if [[ -z "$PAIRS" ]]; then
  PAIRS=$(python3 -c "
import json
with open('user_data/config.json') as f:
    c = json.load(f)
print(','.join(c['exchange']['pair_whitelist']))
")
fi

# Pairs CSV → space-separated for CLI
PAIRS_CLI=${PAIRS//,/ }

# UTC timestamp + timerange
TS=$(date -u +"%Y%m%dT%H%M%SZ")
END=$(date -u +"%Y%m%d")
# BSD (macOS) and GNU (Linux) date both supported
if START=$(date -u -v-"${DAYS}"d +"%Y%m%d" 2>/dev/null); then :;
else START=$(date -u -d "${DAYS} days ago" +"%Y%m%d"); fi
TIMERANGE="${START}-${END}"

RESULT_DIR="user_data/backtest_results/${TS}"
mkdir -p "$RESULT_DIR"
RESULT_JSON_HOST="${RESULT_DIR}/result.json"
RESULT_JSON_CONTAINER="/freqtrade/${RESULT_JSON_HOST}"

cat <<EOF
=========================================
  Backtest Run
-----------------------------------------
  Timestamp:    $TS
  Timerange:    $TIMERANGE  (${DAYS} days)
  Pairs:        $PAIRS
  Strategy:     $STRATEGY
  FreqAI model: $FREQAI_MODEL
  Timeframe:    $TIMEFRAME
  Output:       $RESULT_DIR
=========================================
EOF

# 1. Download data (skippable)
if [[ "$SKIP_DOWNLOAD" != "1" ]]; then
  echo ""
  echo "[1/3] Downloading OHLCV data..."
  docker compose run --rm freqtrade download-data \
    --exchange binance \
    --pairs $PAIRS_CLI \
    --timeframes 5m 15m 1h 4h \
    --days "$DAYS" \
    --trading-mode futures
else
  echo "[1/3] Skipping download (FT_BACKTEST_SKIP_DOWNLOAD=1)"
fi

# 2. Backtesting
echo ""
echo "[2/3] Running backtest..."
docker compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/config.json \
  --strategy "$STRATEGY" \
  --freqaimodel "$FREQAI_MODEL" \
  --timeframe "$TIMEFRAME" \
  --timerange "$TIMERANGE" \
  --export trades \
  --export-filename "$RESULT_JSON_CONTAINER" \
  --breakdown day week month

if [[ ! -f "$RESULT_JSON_HOST" ]]; then
  echo "ERROR: backtest result not produced at $RESULT_JSON_HOST" >&2
  exit 3
fi

# 3. Markdown report
echo ""
echo "[3/3] Generating markdown report..."
set +e
python3 "$SCRIPT_DIR/backtest_report.py" \
  --result "$RESULT_JSON_HOST" \
  --output "${RESULT_DIR}/REPORT.md" \
  --strategy "$STRATEGY" \
  --timerange "$TIMERANGE"
REPORT_RC=$?
set -e

echo ""
echo "Done. Report: ${RESULT_DIR}/REPORT.md"
exit $REPORT_RC
