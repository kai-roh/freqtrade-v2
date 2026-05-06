#!/usr/bin/env bash
# run_hyperopt.sh - 자동화된 하이퍼옵트 실행
#
# Usage:
#   ./scripts/run_hyperopt.sh
#   ./scripts/run_hyperopt.sh --epochs 200 --spaces "buy sell roi"
#   ./scripts/run_hyperopt.sh --days 60 --loss SortinoHyperOptLoss
#
# Env (override defaults):
#   FT_HYPEROPT_EPOCHS, FT_HYPEROPT_SPACES, FT_HYPEROPT_LOSS,
#   FT_HYPEROPT_DAYS, FT_HYPEROPT_RANDOM_STATE, FT_HYPEROPT_TIMEFRAME,
#   FT_HYPEROPT_STRATEGY, FT_HYPEROPT_FREQAI_MODEL
#
# Output:
#   user_data/hyperopt_results/<UTC-ts>/{results.fthypt, REPORT.md, best_params.json}
#
# Exit codes:
#   0 - hyperopt + report OK
#   2 - report 생성 실패 / fthypt 없음
#   3 - hyperopt 실행 실패
#
# 주의: hyperopt는 데이터/에폭에 따라 수십 분 ~ 수 시간 소요. CI에서 돌리지 말 것.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

EPOCHS="${FT_HYPEROPT_EPOCHS:-100}"
SPACES="${FT_HYPEROPT_SPACES:-buy sell}"
LOSS="${FT_HYPEROPT_LOSS:-SharpeHyperOptLoss}"
DAYS="${FT_HYPEROPT_DAYS:-90}"
RANDOM_STATE="${FT_HYPEROPT_RANDOM_STATE:-42}"
TIMEFRAME="${FT_HYPEROPT_TIMEFRAME:-5m}"
STRATEGY="${FT_HYPEROPT_STRATEGY:-KaiBaseStrategy}"
FREQAI_MODEL="${FT_HYPEROPT_FREQAI_MODEL:-LLMEnhancedModel}"
PAIRS="${FT_HYPEROPT_PAIRS:-}"
SKIP_DOWNLOAD="${FT_HYPEROPT_SKIP_DOWNLOAD:-0}"

usage() {
  cat <<EOF
Usage: $0 [options]
  --epochs N             Hyperopt epochs (default: 100)
  --spaces "S1 S2"       Search spaces (default: "buy sell")
  --loss FUNC            Loss function (default: SharpeHyperOptLoss)
                         alts: SortinoHyperOptLoss, ProfitDrawDownHyperOptLoss,
                               CalmarHyperOptLoss, MaxDrawDownHyperOptLoss
  --days N               Lookback days (default: 90)
  --random-state N       Seed for reproducibility (default: 42)
  --timeframe TF         Main timeframe (default: 5m)
  --strategy NAME        Strategy class (default: KaiBaseStrategy)
  --freqaimodel NAME     FreqAI model (default: LLMEnhancedModel)
  --pairs A,B,C          Comma-separated pairs (default: config.json whitelist)
  --skip-download        Reuse existing OHLCV data
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --epochs) EPOCHS="$2"; shift 2;;
    --spaces) SPACES="$2"; shift 2;;
    --loss) LOSS="$2"; shift 2;;
    --days) DAYS="$2"; shift 2;;
    --random-state) RANDOM_STATE="$2"; shift 2;;
    --timeframe) TIMEFRAME="$2"; shift 2;;
    --strategy) STRATEGY="$2"; shift 2;;
    --freqaimodel) FREQAI_MODEL="$2"; shift 2;;
    --pairs) PAIRS="$2"; shift 2;;
    --skip-download) SKIP_DOWNLOAD=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$PAIRS" ]]; then
  PAIRS=$(python3 -c "
import json
with open('user_data/config.json') as f:
    c = json.load(f)
print(','.join(c['exchange']['pair_whitelist']))
")
fi
PAIRS_CLI=${PAIRS//,/ }

TS=$(date -u +"%Y%m%dT%H%M%SZ")
END=$(date -u +"%Y%m%d")
if START=$(date -u -v-"${DAYS}"d +"%Y%m%d" 2>/dev/null); then :;
else START=$(date -u -d "${DAYS} days ago" +"%Y%m%d"); fi
TIMERANGE="${START}-${END}"

RESULT_DIR="user_data/hyperopt_results/${TS}"
mkdir -p "$RESULT_DIR"

cat <<EOF
=========================================
  Hyperopt Run
-----------------------------------------
  Timestamp:    $TS
  Timerange:    $TIMERANGE  (${DAYS} days)
  Pairs:        $PAIRS
  Strategy:     $STRATEGY
  FreqAI model: $FREQAI_MODEL
  Timeframe:    $TIMEFRAME
  Epochs:       $EPOCHS
  Spaces:       $SPACES
  Loss:         $LOSS
  Random state: $RANDOM_STATE
  Output:       $RESULT_DIR
=========================================

NOTE: hyperopt 실행에 수십 분 ~ 수 시간 소요됨. Ctrl+C는 컨테이너 정지로 이어짐.
EOF

# 1. Download data
if [[ "$SKIP_DOWNLOAD" != "1" ]]; then
  echo ""
  echo "[1/3] Downloading OHLCV data..."
  docker compose run --rm freqtrade download-data \
    --exchange binance \
    --pairs $PAIRS_CLI \
    --timeframes 5m 15m 1h \
    --days "$DAYS" \
    --trading-mode futures
else
  echo "[1/3] Skipping download"
fi

# 2. Hyperopt — 결과 .fthypt는 user_data/hyperopt_results/<auto>.fthypt에 자동 저장됨.
#    실행 시작 시점 마커를 만들어 그 이후 생성된 파일만 캡처.
MARKER="${RESULT_DIR}/.hyperopt_start"
touch "$MARKER"

echo ""
echo "[2/3] Running hyperopt (${EPOCHS} epochs, this can take a while)..."
set +e
docker compose run --rm freqtrade hyperopt \
  --config /freqtrade/user_data/config.json \
  --strategy "$STRATEGY" \
  --freqaimodel "$FREQAI_MODEL" \
  --timeframe "$TIMEFRAME" \
  --timerange "$TIMERANGE" \
  --epochs "$EPOCHS" \
  --spaces $SPACES \
  --hyperopt-loss "$LOSS" \
  --random-state "$RANDOM_STATE"
HYPEROPT_RC=$?
set -e

if [[ $HYPEROPT_RC -ne 0 ]]; then
  echo "ERROR: hyperopt exited with code $HYPEROPT_RC" >&2
  rm -f "$MARKER"
  exit 3
fi

# 가장 최근 .fthypt 파일을 우리 결과 디렉토리로 이동
NEW_FILE=$(find user_data/hyperopt_results -maxdepth 1 -name "*.fthypt" -type f -newer "$MARKER" 2>/dev/null | head -1)
rm -f "$MARKER"

if [[ -z "$NEW_FILE" || ! -f "$NEW_FILE" ]]; then
  echo "ERROR: hyperopt result .fthypt not produced" >&2
  exit 2
fi

mv "$NEW_FILE" "${RESULT_DIR}/results.fthypt"

# 3. Markdown report + best_params.json
echo ""
echo "[3/3] Generating markdown report..."
set +e
python3 "$SCRIPT_DIR/hyperopt_report.py" \
  --results "${RESULT_DIR}/results.fthypt" \
  --output "${RESULT_DIR}/REPORT.md" \
  --best-params "${RESULT_DIR}/best_params.json" \
  --strategy "$STRATEGY" \
  --epochs "$EPOCHS" \
  --loss "$LOSS" \
  --timerange "$TIMERANGE"
REPORT_RC=$?
set -e

echo ""
echo "Done."
echo "  Report:       ${RESULT_DIR}/REPORT.md"
echo "  Best params:  ${RESULT_DIR}/best_params.json"
echo ""
echo "Apply best params via:"
echo "  docker compose run --rm freqtrade hyperopt-show --best"
exit $REPORT_RC
