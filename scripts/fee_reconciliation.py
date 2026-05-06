#!/usr/bin/env python3
"""
fee_reconciliation.py - 백테스트 가정 수수료 vs 실제 청구 수수료 대사

실행: python3 scripts/fee_reconciliation.py
또는:  docker compose run --rm freqtrade python /freqtrade/scripts/fee_reconciliation.py

기능:
1. Freqtrade DB(tradesv3.sqlite)에서 최근 N일 거래 조회
2. Binance API에서 동일 거래의 실제 commission 조회
3. 차이 계산 후 권장 fee 보정값 출력
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("/freqtrade/user_data/tradesv3.sqlite")
LOOKBACK_DAYS = 7


def query_local_trades():
    """로컬 DB에서 최근 거래 조회"""
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return []

    cutoff = (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT id, pair, is_open, open_date, close_date,
                   open_rate, close_rate, amount, fee_open, fee_close,
                   stake_amount, close_profit_abs
            FROM trades
            WHERE close_date >= ?
            ORDER BY close_date DESC
        """, (cutoff,))
        rows = cur.fetchall()
    finally:
        con.close()
    return rows


def fetch_binance_actual_fees():
    """
    Binance API에서 실제 commission 조회.
    실제 운영 시 ccxt 또는 python-binance 활용.
    """
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        print("Binance API keys not set, skipping real fee fetch")
        return None

    try:
        import ccxt
        exchange = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })

        since = exchange.parse8601(
            (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        )

        all_trades = []
        for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
                     "BNB/USDT:USDT", "XRP/USDT:USDT"]:
            try:
                trades = exchange.fetch_my_trades(pair, since=since, limit=100)
                all_trades.extend(trades)
            except Exception as e:
                print(f"Skip {pair}: {e}")
        return all_trades
    except ImportError:
        print("ccxt not installed in this environment, run inside docker")
        return None
    except Exception as e:
        print(f"Binance fetch failed: {e}")
        return None


def main():
    print(f"=== Fee Reconciliation (last {LOOKBACK_DAYS} days) ===\n")

    local_trades = query_local_trades()
    if not local_trades:
        print("No local trades found.")
        return

    total_local_fee = 0.0
    total_volume = 0.0

    for t in local_trades:
        (tid, pair, is_open, open_d, close_d, open_r, close_r,
         amount, fee_open, fee_close, stake, profit) = t

        if is_open:
            continue

        notional = (open_r + close_r) * amount
        local_fee = (fee_open + (fee_close or 0)) * notional / 2
        total_local_fee += local_fee
        total_volume += notional

    print(f"Local trades: {len(local_trades)}")
    print(f"Total notional: ${total_volume:,.2f}")
    print(f"Local fee assumption: ${total_local_fee:,.4f}")
    avg_local_rate = (total_local_fee / total_volume * 100) if total_volume > 0 else 0
    print(f"Local effective fee rate: {avg_local_rate:.4f}%\n")

    binance_trades = fetch_binance_actual_fees()
    if binance_trades is None:
        print("Skipping Binance comparison.\n")
        return

    total_real_fee = sum(float(t.get("fee", {}).get("cost", 0)) for t in binance_trades)
    total_real_volume = sum(float(t.get("cost", 0)) for t in binance_trades)
    avg_real_rate = (total_real_fee / total_real_volume * 100) if total_real_volume > 0 else 0

    print(f"Binance real trades: {len(binance_trades)}")
    print(f"Real total fee: ${total_real_fee:,.4f}")
    print(f"Real effective fee rate: {avg_real_rate:.4f}%\n")

    if total_local_fee > 0:
        diff_pct = (total_real_fee - total_local_fee) / total_local_fee * 100
        print(f"Difference: {diff_pct:+.2f}%")

        if abs(diff_pct) > 5:
            recommended = avg_real_rate / 100 * 1.5  # 1.5배 안전마진
            print(f"\n>>> RECOMMENDATION: Update config.json 'fee' to {recommended:.6f}")
        else:
            print("\n>>> Within tolerance, no update needed.")


if __name__ == "__main__":
    main()
