#!/bin/bash
# control.sh - Freqtrade 수동 제어 (이동 시 끊김 대응)
#
# 사용:
#   ./scripts/control.sh status         # 현재 상태
#   ./scripts/control.sh start          # 시작
#   ./scripts/control.sh stop_safe      # 신규 진입 차단 + 기존 포지션 유지
#   ./scripts/control.sh resume         # stop_safe 해제
#   ./scripts/control.sh emergency_close # 즉시 전체 청산 + 정지
#   ./scripts/control.sh restart        # 재시작
#   ./scripts/control.sh logs           # 실시간 로그

set -e

cd "$(dirname "$0")/.." || exit 1

if [ ! -f .env ]; then
    echo "Error: .env not found"
    exit 1
fi

source .env

API_BASE="http://localhost:8080/api/v1"
AUTH="-u ${FREQTRADE_USERNAME}:${FREQTRADE_PASSWORD}"

case "$1" in
    status)
        echo "=== Container ==="
        docker compose ps
        echo ""
        echo "=== Bot status ==="
        curl -s $AUTH "$API_BASE/status" | jq '.[] | {pair, profit_pct, stake_amount}' 2>/dev/null || \
            curl -s $AUTH "$API_BASE/status"
        echo ""
        echo "=== Performance ==="
        curl -s $AUTH "$API_BASE/profit" | jq '.' 2>/dev/null || \
            curl -s $AUTH "$API_BASE/profit"
        ;;

    start)
        echo "Starting freqtrade..."
        docker compose up -d
        sleep 3
        curl -s -X POST $AUTH "$API_BASE/start" | jq '.' 2>/dev/null
        ;;

    stop_safe)
        echo "Stopping new entries (existing positions remain)..."
        curl -s -X POST $AUTH "$API_BASE/stopentry" | jq '.' 2>/dev/null
        echo "Bot will not open new trades. Existing trades follow exit signals."
        echo "Use 'resume' to re-enable entries."
        ;;

    resume)
        echo "Resuming entries..."
        curl -s -X POST $AUTH "$API_BASE/start" | jq '.' 2>/dev/null
        ;;

    emergency_close)
        echo "!!! EMERGENCY: Closing all positions and stopping bot !!!"
        read -p "Confirm? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            curl -s -X POST $AUTH "$API_BASE/forceexit" \
                -H "Content-Type: application/json" \
                -d '{"tradeid":"all","ordertype":"market"}' | jq '.' 2>/dev/null
            sleep 5
            curl -s -X POST $AUTH "$API_BASE/stop" | jq '.' 2>/dev/null
            echo "All positions closed and bot stopped."
        else
            echo "Cancelled."
        fi
        ;;

    restart)
        echo "Restarting freqtrade..."
        docker compose restart freqtrade
        ;;

    logs)
        docker compose logs -f --tail 100 freqtrade
        ;;

    daily_report)
        # 통합 리포트: PnL + 일별 breakdown + fee 대사 + Claude 비용 + 알림
        # exit code: 0=clean / 1=alerts / 2=API down
        if docker compose ps --status running freqtrade 2>/dev/null | grep -q freqtrade; then
            docker compose exec -T freqtrade \
                python /freqtrade/scripts/daily_report.py "${@:2}"
        else
            # 컨테이너가 떠있지 않으면 ephemeral run
            docker compose run --rm -T freqtrade \
                python /freqtrade/scripts/daily_report.py "${@:2}"
        fi
        ;;

    *)
        echo "Usage: $0 {status|start|stop_safe|resume|emergency_close|restart|logs|daily_report}"
        echo ""
        echo "Manual flow when leaving PC:"
        echo "  Short trip (<1h):   ./control.sh stop_safe"
        echo "  Long trip (>4h):    ./control.sh emergency_close"
        echo "  Return:              ./control.sh resume  (or  start)"
        exit 1
        ;;
esac
