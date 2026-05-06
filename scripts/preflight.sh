#!/usr/bin/env bash
# preflight.sh - 운영 시작 전 환경 점검 (1-stop check)
#
# Usage:
#   ./scripts/preflight.sh
#   ./scripts/preflight.sh --init-jwt        # JWT_SECRET이 placeholder면 자동 생성
#   ./scripts/preflight.sh --strict          # WARN도 실패로 처리
#
# 검증 항목:
#   - .env 존재 + 필수 키 (Binance, Anthropic, WebUI, JWT)
#   - placeholder 패턴 감지 (xxxxxxxx, your_*_here, change_this_*, run_openssl_*)
#   - 키 길이/포맷 sanity check
#   - config.json 유효성 + dry_run 상태
#   - Docker daemon 동작
#   - .gitignore가 .env 보호
#   - Telegram 토큰 (선택)
#
# Exit codes:
#   0 - 모든 체크 PASS (또는 WARN만 있고 --strict 아님)
#   1 - WARN 1+ 이고 --strict
#   2 - FAIL 1+ (운영 불가)

set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env"
INIT_JWT=0
STRICT=0

usage() {
  cat <<EOF
Usage: $0 [options]
  --init-jwt   Auto-generate JWT_SECRET if it is placeholder/empty and write to .env
  --strict     Treat WARN as failure (exit 1)
  -h, --help   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-jwt) INIT_JWT=1; shift;;
    --strict) STRICT=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown: $1" >&2; usage; exit 2;;
  esac
done

PASS=0; WARN=0; FAIL=0
check() {
  local label="$1" status="$2" msg="${3:-}"
  case "$status" in
    PASS) printf "  [ PASS ] %-30s %s\n" "$label" "$msg"; PASS=$((PASS+1));;
    WARN) printf "  [ WARN ] %-30s %s\n" "$label" "$msg"; WARN=$((WARN+1));;
    FAIL) printf "  [ FAIL ] %-30s %s\n" "$label" "$msg"; FAIL=$((FAIL+1));;
  esac
}

is_placeholder() {
  local v="$1"
  [[ -z "$v" ]] && return 0
  case "$v" in
    *xxxxxxxx*) return 0;;
    your_*_here) return 0;;
    change_this_*) return 0;;
    run_openssl_rand_hex_32_and_paste_here) return 0;;
  esac
  return 1
}

echo "=== Preflight Check ==="
echo ""

# 1. .env 존재
if [[ ! -f "$ENV_FILE" ]]; then
  check ".env exists" FAIL "missing — copy .env.example → .env"
  echo ""
  echo "Run: cp .env.example .env"
  exit 2
fi
check ".env exists" PASS

# .env 로드
set -a
# shellcheck disable=SC1090,SC1091
. "$ENV_FILE"
set +a

# 2. Binance keys
if is_placeholder "${BINANCE_API_KEY:-}"; then
  check "BINANCE_API_KEY" FAIL "placeholder/empty"
elif [[ ${#BINANCE_API_KEY} -ne 64 ]]; then
  check "BINANCE_API_KEY" WARN "length=${#BINANCE_API_KEY} (Binance keys are typically 64)"
else
  check "BINANCE_API_KEY" PASS "len=64"
fi

if is_placeholder "${BINANCE_API_SECRET:-}"; then
  check "BINANCE_API_SECRET" FAIL "placeholder/empty"
elif [[ ${#BINANCE_API_SECRET} -ne 64 ]]; then
  check "BINANCE_API_SECRET" WARN "length=${#BINANCE_API_SECRET}"
else
  check "BINANCE_API_SECRET" PASS "len=64"
fi

# 3. Anthropic
if is_placeholder "${ANTHROPIC_API_KEY:-}"; then
  check "ANTHROPIC_API_KEY" FAIL "placeholder/empty"
elif [[ "$ANTHROPIC_API_KEY" != sk-ant-* ]]; then
  check "ANTHROPIC_API_KEY" WARN "expected to start with 'sk-ant-'"
else
  check "ANTHROPIC_API_KEY" PASS
fi

# 4. Freqtrade Web UI credentials
if is_placeholder "${FREQTRADE_USERNAME:-}"; then
  check "FREQTRADE_USERNAME" FAIL "placeholder/empty"
else
  check "FREQTRADE_USERNAME" PASS "($FREQTRADE_USERNAME)"
fi

if is_placeholder "${FREQTRADE_PASSWORD:-}"; then
  check "FREQTRADE_PASSWORD" FAIL "placeholder — change_this_strong_password 패턴"
elif [[ ${#FREQTRADE_PASSWORD} -lt 12 ]]; then
  check "FREQTRADE_PASSWORD" WARN "length=${#FREQTRADE_PASSWORD} (12+ recommended)"
else
  check "FREQTRADE_PASSWORD" PASS "len=${#FREQTRADE_PASSWORD}"
fi

# 5. JWT_SECRET (옵션: --init-jwt면 자동 생성)
JWT_NEEDS_INIT=0
if is_placeholder "${JWT_SECRET:-}"; then
  JWT_NEEDS_INIT=1
elif [[ ${#JWT_SECRET} -lt 32 ]]; then
  check "JWT_SECRET" WARN "length=${#JWT_SECRET} (32+ hex recommended)"
else
  check "JWT_SECRET" PASS "len=${#JWT_SECRET}"
fi

if [[ $JWT_NEEDS_INIT -eq 1 ]]; then
  if [[ $INIT_JWT -eq 1 ]]; then
    if ! command -v openssl >/dev/null 2>&1; then
      check "JWT_SECRET" FAIL "openssl not found — install or paste manually"
    else
      new_jwt=$(openssl rand -hex 32)
      if grep -q "^JWT_SECRET=" "$ENV_FILE"; then
        # macOS BSD sed vs GNU sed
        if [[ "$OSTYPE" == "darwin"* ]]; then
          sed -i '' "s|^JWT_SECRET=.*|JWT_SECRET=${new_jwt}|" "$ENV_FILE"
        else
          sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${new_jwt}|" "$ENV_FILE"
        fi
      else
        echo "JWT_SECRET=${new_jwt}" >> "$ENV_FILE"
      fi
      check "JWT_SECRET" PASS "auto-generated (64 hex chars) → .env updated"
    fi
  else
    check "JWT_SECRET" FAIL "placeholder — re-run with --init-jwt to auto-generate, or paste 'openssl rand -hex 32' output"
  fi
fi

# 6. Telegram (선택)
if [[ -z "${TELEGRAM_TOKEN:-}" ]]; then
  check "TELEGRAM_TOKEN" WARN "not set — daily_report --telegram disabled"
else
  check "TELEGRAM_TOKEN" PASS "configured"
fi

# 7. config.json
if [[ ! -f user_data/config.json ]]; then
  check "config.json" FAIL "missing"
elif ! python3 -c "import json; json.load(open('user_data/config.json'))" 2>/dev/null; then
  check "config.json" FAIL "invalid JSON"
else
  dry_run=$(python3 -c "import json; print(json.load(open('user_data/config.json'))['dry_run'])")
  stake=$(python3 -c "import json; print(json.load(open('user_data/config.json'))['stake_amount'])")
  if [[ "$dry_run" == "True" ]]; then
    check "config.json" PASS "dry_run=true, stake_amount=$stake"
  else
    check "config.json" WARN "dry_run=false — LIVE TRADING ENABLED, stake=$stake"
  fi
fi

# 8. Docker
if ! command -v docker >/dev/null 2>&1; then
  check "docker" FAIL "not installed (brew install --cask docker)"
elif ! docker info >/dev/null 2>&1; then
  check "docker daemon" FAIL "not running — open Docker Desktop"
else
  check "docker daemon" PASS

  # docker compose 가능 여부
  if docker compose version >/dev/null 2>&1; then
    check "docker compose" PASS
  else
    check "docker compose" FAIL "docker compose plugin not available"
  fi
fi

# 9. .gitignore — .env 보호
if [[ ! -f .gitignore ]]; then
  check ".gitignore" WARN "missing"
elif grep -qE "^\.env\b|^\.env$" .gitignore 2>/dev/null; then
  check ".gitignore .env" PASS
else
  check ".gitignore .env" WARN ".env not explicitly ignored — risk of commit"
fi

# 10. user_data 디렉토리 구조
for d in user_data/strategies user_data/freqaimodels user_data/llm; do
  if [[ -d "$d" ]]; then
    check "$d" PASS
  else
    check "$d" FAIL "directory missing"
  fi
done

echo ""
echo "=== Summary ==="
printf "  PASS: %d\n  WARN: %d\n  FAIL: %d\n" "$PASS" "$WARN" "$FAIL"

if [[ $FAIL -gt 0 ]]; then
  echo ""
  echo "Preflight FAILED — fix above before running 'docker compose up'"
  exit 2
fi

if [[ $WARN -gt 0 ]]; then
  echo ""
  if [[ $STRICT -eq 1 ]]; then
    echo "Preflight WARN (strict mode → fail). Resolve warnings or drop --strict."
    exit 1
  else
    echo "Preflight passed with warnings. Review and proceed with: docker compose up -d"
    exit 0
  fi
fi

echo ""
echo "All checks passed. Ready: docker compose up -d"
exit 0
