#!/usr/bin/env bash
# test.sh - pytest 단위 테스트 실행 (호스트 Python)
#
# Usage:
#   ./scripts/test.sh                  # 전체
#   ./scripts/test.sh tests/test_cost_tracker.py
#   ./scripts/test.sh -k cache         # 키워드 매칭
#
# 의존성: requirements-dev.txt (pytest)
#   pip install -r requirements-dev.txt

set -e
cd "$(dirname "$0")/.."

if ! python3 -c "import pytest" 2>/dev/null; then
    echo "pytest not installed. Run: pip install -r requirements-dev.txt" >&2
    exit 1
fi

exec python3 -m pytest "$@"
