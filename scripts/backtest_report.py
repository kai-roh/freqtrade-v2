#!/usr/bin/env python3
"""
backtest_report.py - Freqtrade 백테스트 결과 → 마크다운 리포트

입력:  --result  (freqtrade --export-filename 결과 JSON)
출력:  --output  마크다운

Live-Gate 판정:
- Sharpe ≥ 1.0
- MDD ≤ 15%

Exit code:
  0 - gate PASS
  1 - gate FAIL (리포트는 정상 생성됨)
  2 - 입력 오류 / 결과 파일 없음
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GATE_SHARPE_MIN = 1.0
GATE_MDD_MAX = 0.15  # 15%


def _coerce_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct(x: Any, scale_hint: str = "ratio") -> str:
    """
    scale_hint:
      - "ratio"   : 0.05 → +5.00%
      - "percent" : 5.0  → +5.00% (이미 퍼센트 단위)
    """
    f = _coerce_float(x)
    if f is None:
        return "—"
    if scale_hint == "percent":
        return f"{f:+.2f}%"
    return f"{f * 100:+.2f}%"


def _f(x: Any, n: int = 4) -> str:
    f = _coerce_float(x)
    return "—" if f is None else f"{f:.{n}f}"


def _i(x: Any) -> str:
    f = _coerce_float(x)
    return "—" if f is None else f"{int(f):,}"


def _pick(d: dict, *keys, default=None):
    """여러 후보 키 중 첫 hit 반환 (freqtrade 버전별 필드명 차이 흡수)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--result", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--timerange", required=True)
    args = p.parse_args()

    result_path = Path(args.result)
    if not result_path.exists():
        print(f"ERROR: result file not found: {result_path}", file=sys.stderr)
        return 2

    try:
        with open(result_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: result JSON invalid: {e}", file=sys.stderr)
        return 2

    strategies: dict = data.get("strategy", {}) or {}
    s = strategies.get(args.strategy)
    chosen_name = args.strategy
    if s is None and strategies:
        chosen_name, s = next(iter(strategies.items()))
    if s is None:
        print(f"ERROR: no strategy results in {result_path}", file=sys.stderr)
        return 2

    total_trades = _coerce_float(_pick(s, "total_trades", default=0)) or 0
    profit_total_ratio = _pick(s, "profit_total", "profit_total_ratio")
    profit_total_abs = _pick(s, "profit_total_abs", default=0.0)
    sharpe = _pick(s, "sharpe", "sharpe_ratio")
    sortino = _pick(s, "sortino")
    mdd_account = _pick(
        s,
        "max_drawdown_account",
        "max_drawdown_abs_pct",
        "max_drawdown",
        default=0.0,
    )
    wins = int(_coerce_float(_pick(s, "wins", default=0)) or 0)
    losses = int(_coerce_float(_pick(s, "losses", default=0)) or 0)
    draws = int(_coerce_float(_pick(s, "draws", default=0)) or 0)
    win_rate = (wins / total_trades) if total_trades > 0 else None
    avg_profit = _pick(s, "profit_mean", "profit_mean_pct")

    backtest_days = _pick(s, "backtest_days")
    starting_balance = _pick(s, "starting_balance")
    final_balance = _pick(s, "final_balance")
    per_pair = _pick(s, "results_per_pair", default=[]) or []
    enter_tag = _pick(s, "enter_tag", "enter_tag_summary", default=[]) or []
    exit_reason = _pick(s, "exit_reason_summary", default=[]) or []

    sharpe_f = _coerce_float(sharpe)
    mdd_f = _coerce_float(mdd_account)
    sharpe_ok = sharpe_f is not None and sharpe_f >= GATE_SHARPE_MIN
    mdd_ok = mdd_f is not None and mdd_f <= GATE_MDD_MAX
    gate_ok = sharpe_ok and mdd_ok

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# Backtest Report — {chosen_name}")
    lines.append("")
    lines.append(f"- **Generated**: {now}")
    lines.append(
        f"- **Timerange**: {args.timerange}"
        + (f" ({int(backtest_days)} days)" if backtest_days else "")
    )
    lines.append(f"- **Result file**: `{result_path}`")
    lines.append("")
    lines.append("## Headline Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total trades | {_i(total_trades)} |")
    lines.append(f"| Win rate | {_pct(win_rate)} ({wins}W / {losses}L / {draws}D) |")
    lines.append(
        f"| Total profit | {_pct(profit_total_ratio)} " f"({_f(profit_total_abs, 2)} {{stake}}) |"
    )
    lines.append(f"| Avg profit per trade | {_pct(avg_profit)} |")
    lines.append(f"| Max drawdown | {_pct(mdd_account)} |")
    lines.append(f"| Sharpe | {_f(sharpe, 3)} |")
    lines.append(f"| Sortino | {_f(sortino, 3)} |")
    if starting_balance is not None:
        lines.append(f"| Starting balance | {_f(starting_balance, 2)} |")
    if final_balance is not None:
        lines.append(f"| Final balance | {_f(final_balance, 2)} |")
    lines.append("")

    lines.append("## Live-Gate Decision")
    lines.append("")
    lines.append(
        f"- Sharpe ≥ {GATE_SHARPE_MIN}: " f"{'PASS' if sharpe_ok else 'FAIL'} (got {_f(sharpe, 3)})"
    )
    lines.append(
        f"- MDD ≤ {GATE_MDD_MAX*100:.0f}%: "
        f"{'PASS' if mdd_ok else 'FAIL'} (got {_pct(mdd_account)})"
    )
    lines.append("")
    lines.append(
        f"**Overall: {'PASS — eligible for next phase' if gate_ok else 'FAIL — keep dry-run / re-tune'}**"
    )
    lines.append("")

    if per_pair:
        lines.append("## Per-Pair Results")
        lines.append("")
        lines.append("| Pair | Trades | Profit | Profit Abs | Avg Duration |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in per_pair:
            pair = _pick(r, "key", "pair", default="—")
            trades = _pick(r, "trades", default=0)
            ppct = _pick(r, "profit_total_pct", "profit_total")
            # results_per_pair는 freqtrade가 percent 단위(0~100)로 주는 케이스가 많음
            scale = (
                "percent"
                if (_coerce_float(ppct) is not None and abs(_coerce_float(ppct) or 0) > 1.0)
                else "ratio"
            )
            pabs = _pick(r, "profit_total_abs", default=0)
            dur = _pick(r, "duration_avg", default="—")
            lines.append(f"| {pair} | {_i(trades)} | {_pct(ppct, scale)} | {_f(pabs, 2)} | {dur} |")
        lines.append("")

    if enter_tag:
        lines.append("## Enter Tags")
        lines.append("")
        lines.append("| Tag | Trades | Profit |")
        lines.append("|---|---:|---:|")
        for r in enter_tag:
            tag = _pick(r, "key", "enter_tag", default="—")
            trades = _pick(r, "trades", default=0)
            ppct = _pick(r, "profit_total_pct", "profit_total")
            scale = (
                "percent"
                if (_coerce_float(ppct) is not None and abs(_coerce_float(ppct) or 0) > 1.0)
                else "ratio"
            )
            lines.append(f"| {tag} | {_i(trades)} | {_pct(ppct, scale)} |")
        lines.append("")

    if exit_reason:
        lines.append("## Exit Reasons")
        lines.append("")
        lines.append("| Reason | Trades | Profit |")
        lines.append("|---|---:|---:|")
        for r in exit_reason:
            reason = _pick(r, "key", "exit_reason", default="—")
            trades = _pick(r, "trades", default=0)
            ppct = _pick(r, "profit_total_pct", "profit_total")
            scale = (
                "percent"
                if (_coerce_float(ppct) is not None and abs(_coerce_float(ppct) or 0) > 1.0)
                else "ratio"
            )
            lines.append(f"| {reason} | {_i(trades)} | {_pct(ppct, scale)} |")
        lines.append("")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))

    # Summary to stdout
    print()
    print("=== Summary ===")
    print(f"Trades:  {int(total_trades)}")
    print(f"Profit:  {_pct(profit_total_ratio)}")
    print(f"MDD:     {_pct(mdd_account)}")
    print(f"Sharpe:  {_f(sharpe, 3)}")
    print(f"Gate:    {'PASS' if gate_ok else 'FAIL'}")
    print(f"Report:  {out_path}")

    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
