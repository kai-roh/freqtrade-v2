#!/usr/bin/env python3
"""
fee_reconciliation.py - 백테스트 가정 수수료 vs 실제 청구 수수료 대사

CLI:
    docker compose run --rm freqtrade python /freqtrade/scripts/fee_reconciliation.py
    docker compose run --rm freqtrade python /freqtrade/scripts/fee_reconciliation.py --days 14

Library:
    from fee_reconciliation import compute_reconciliation
    result = compute_reconciliation(lookback_days=7)

기능:
1. Freqtrade DB(tradesv3.sqlite)에서 최근 N일 거래 조회
2. Binance API에서 동일 거래의 실제 commission 조회
3. 차이 계산 후 권장 fee 보정값 반환

Exit code:
    0: 정상 또는 데이터 부족
    1: 차이 5% 초과 (보정 필요)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.getenv("FT_DB_PATH", "/freqtrade/user_data/tradesv3.sqlite"))
DEFAULT_LOOKBACK_DAYS = 7
TOLERANCE_PCT = 5.0  # 차이가 이 % 초과 시 alert


def _config_pairs() -> list[str]:
    """config.json의 pair_whitelist를 반환. 실패 시 폴백 5종."""
    cfg_path = Path(os.getenv("FT_CONFIG_PATH",
                              "/freqtrade/user_data/config.json"))
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        pairs = cfg.get("exchange", {}).get("pair_whitelist") or []
        if pairs:
            return list(pairs)
    except Exception:
        pass
    return [
        "BTC/USDT:USDT", "ETH/USDT:USDT",
        "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT",
    ]


def query_local_trades(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[tuple]:
    """로컬 DB에서 최근 거래 조회. 미존재/오류 시 빈 리스트."""
    if not DB_PATH.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT id, pair, is_open, open_date, close_date,
                   open_rate, close_rate, amount, fee_open, fee_close,
                   stake_amount, close_profit_abs
            FROM trades
            WHERE close_date >= ?
            ORDER BY close_date DESC
        """, (cutoff,))
        return cur.fetchall()
    finally:
        con.close()


def fetch_binance_actual_fees(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    pairs: Optional[list[str]] = None,
) -> Optional[list[dict]]:
    """
    Binance API에서 실제 commission 조회.
    None 반환 시 비교 불가(키 미설정/네트워크 실패/ccxt 미설치).
    """
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return None

    try:
        import ccxt
    except ImportError:
        return None

    try:
        exchange = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        since = exchange.parse8601(
            (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        )
        all_trades: list[dict] = []
        for pair in pairs or _config_pairs():
            try:
                trades = exchange.fetch_my_trades(pair, since=since, limit=100)
                all_trades.extend(trades)
            except Exception as e:
                print(f"[fee_recon] skip {pair}: {e}", file=sys.stderr)
        return all_trades
    except Exception as e:
        print(f"[fee_recon] binance fetch failed: {e}", file=sys.stderr)
        return None


def compute_reconciliation(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    pairs: Optional[list[str]] = None,
) -> dict:
    """
    수수료 대사 결과 dict 반환. import 가능 인터페이스.

    return:
        {
          "lookback_days": int,
          "local": {"trades": int, "notional_usd": float,
                    "fee_usd": float, "effective_rate_pct": float},
          "real":  {"trades": int, "fee_usd": float,
                    "effective_rate_pct": float} | None,
          "diff_pct": float | None,
          "recommended_fee": float | None,
          "tolerance_pct": float,
          "status": "ok" | "tolerance_exceeded" | "no_local_trades" | "no_binance_data",
        }
    """
    out: dict = {
        "lookback_days": lookback_days,
        "local": {"trades": 0, "notional_usd": 0.0,
                  "fee_usd": 0.0, "effective_rate_pct": 0.0},
        "real": None,
        "diff_pct": None,
        "recommended_fee": None,
        "tolerance_pct": TOLERANCE_PCT,
        "status": "no_local_trades",
    }

    local_trades = query_local_trades(lookback_days)
    if not local_trades:
        return out

    total_local_fee = 0.0
    total_volume = 0.0
    closed_n = 0
    for t in local_trades:
        (_tid, _pair, is_open, _od, _cd, open_r, close_r,
         amount, fee_open, fee_close, _stake, _profit) = t
        if is_open:
            continue
        if close_r is None or open_r is None or amount is None:
            continue
        notional = (open_r + close_r) * amount
        local_fee = (fee_open + (fee_close or 0)) * notional / 2
        total_local_fee += local_fee
        total_volume += notional
        closed_n += 1

    out["local"] = {
        "trades": closed_n,
        "notional_usd": total_volume,
        "fee_usd": total_local_fee,
        "effective_rate_pct": (total_local_fee / total_volume * 100) if total_volume > 0 else 0.0,
    }

    binance_trades = fetch_binance_actual_fees(lookback_days, pairs)
    if binance_trades is None:
        out["status"] = "no_binance_data"
        return out

    total_real_fee = sum(float((t.get("fee") or {}).get("cost", 0) or 0)
                         for t in binance_trades)
    total_real_volume = sum(float(t.get("cost", 0) or 0) for t in binance_trades)
    real_rate = (total_real_fee / total_real_volume * 100) if total_real_volume > 0 else 0.0
    out["real"] = {
        "trades": len(binance_trades),
        "fee_usd": total_real_fee,
        "effective_rate_pct": real_rate,
    }

    if total_local_fee > 0:
        diff_pct = (total_real_fee - total_local_fee) / total_local_fee * 100
        out["diff_pct"] = diff_pct
        if abs(diff_pct) > TOLERANCE_PCT:
            out["recommended_fee"] = real_rate / 100 * 1.5  # 1.5배 안전마진
            out["status"] = "tolerance_exceeded"
        else:
            out["status"] = "ok"

    return out


def _print_human(r: dict) -> None:
    print(f"=== Fee Reconciliation (last {r['lookback_days']} days) ===\n")
    loc = r["local"]
    print(f"Local trades: {loc['trades']}")
    print(f"Local notional: ${loc['notional_usd']:,.2f}")
    print(f"Local fee assumption: ${loc['fee_usd']:,.4f}")
    print(f"Local effective fee rate: {loc['effective_rate_pct']:.4f}%\n")

    if r["status"] == "no_local_trades":
        print("No local trades found.")
        return
    if r["status"] == "no_binance_data":
        print("Binance comparison skipped (no API key, ccxt missing, or fetch failed).\n")
        return

    real = r["real"] or {}
    print(f"Binance real trades: {real.get('trades', 0)}")
    print(f"Real total fee: ${real.get('fee_usd', 0):,.4f}")
    print(f"Real effective fee rate: {real.get('effective_rate_pct', 0):.4f}%\n")

    diff = r.get("diff_pct")
    if diff is None:
        print("No diff computed (zero local fee).")
        return
    print(f"Difference: {diff:+.2f}%")
    if r["status"] == "tolerance_exceeded":
        print(f"\n>>> RECOMMENDATION: Update config.json 'fee' to {r['recommended_fee']:.6f}")
    else:
        print("\n>>> Within tolerance, no update needed.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    p.add_argument("--json", action="store_true",
                   help="Emit JSON to stdout instead of human format")
    args = p.parse_args()

    result = compute_reconciliation(lookback_days=args.days)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)
    return 1 if result["status"] == "tolerance_exceeded" else 0


if __name__ == "__main__":
    sys.exit(main())
