#!/usr/bin/env python3
"""
hyperopt_report.py - Freqtrade hyperopt(.fthypt) → 마크다운 리포트 + best_params

입력:  --results  (JSON Lines, 각 줄=epoch)
출력:  --output       마크다운
       --best-params  JSON (loss 최소 epoch의 params_dict)

Best 선정 기준:
  loss 가 가장 작은 epoch (freqtrade hyperopt 의 loss는 "낮을수록 좋음")

Exit code:
  0 - report 생성됨, best 식별
  2 - 입력 오류 / 유효 epoch 0건
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _coerce_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _f(x: Any, n: int = 4) -> str:
    f = _coerce_float(x)
    return "—" if f is None else f"{f:.{n}f}"


def _pct(x: Any, scale: str = "ratio") -> str:
    f = _coerce_float(x)
    if f is None:
        return "—"
    if scale == "percent":
        return f"{f:+.2f}%"
    return f"{f * 100:+.2f}%"


def parse_fthypt(path: Path) -> list[dict]:
    """JSON Lines 파일을 파싱해 유효 epoch dict 리스트 반환."""
    epochs: list[dict] = []
    if not path.exists():
        return epochs
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    epochs.append(obj)
            except json.JSONDecodeError:
                continue
    return epochs


def best_epoch(epochs: list[dict]) -> Optional[dict]:
    """loss 최소 epoch 반환. loss 없으면 None."""
    candidates = [e for e in epochs if _coerce_float(e.get("loss")) is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda e: float(e["loss"]))


def _metric_row(e: dict) -> dict:
    """리포트 표 한 줄용 메트릭 추출 (필드명 폴백 포함)."""
    m = e.get("results_metrics") or {}
    return {
        "epoch": e.get("current_epoch", "—"),
        "loss": _coerce_float(e.get("loss")),
        "trades": m.get("total_trades"),
        "profit_total": m.get("profit_total", m.get("profit_total_ratio")),
        "sharpe": m.get("sharpe", m.get("sharpe_ratio")),
        "sortino": m.get("sortino"),
        "mdd": m.get("max_drawdown_account",
                     m.get("max_drawdown_abs_pct", m.get("max_drawdown"))),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--results", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--best-params", required=True,
                   help="path to write best epoch's params_dict as JSON")
    p.add_argument("--strategy", required=True)
    p.add_argument("--epochs", required=True)
    p.add_argument("--loss", required=True)
    p.add_argument("--timerange", required=True)
    args = p.parse_args()

    results_path = Path(args.results)
    epochs = parse_fthypt(results_path)
    if not epochs:
        print(f"ERROR: no valid epochs in {results_path}", file=sys.stderr)
        return 2

    best = best_epoch(epochs)
    if best is None:
        print("ERROR: no epoch with a numeric loss", file=sys.stderr)
        return 2

    # Top 5 by loss (ascending)
    sorted_epochs = sorted(
        [e for e in epochs if _coerce_float(e.get("loss")) is not None],
        key=lambda e: float(e["loss"]),
    )[:5]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = []
    L.append(f"# Hyperopt Report — {args.strategy}")
    L.append("")
    L.append(f"- **Generated**: {now}")
    L.append(f"- **Timerange**: {args.timerange}")
    L.append(f"- **Epochs requested / valid**: {args.epochs} / {len(epochs)}")
    L.append(f"- **Loss function**: `{args.loss}`")
    L.append(f"- **Result file**: `{results_path}`")
    L.append("")

    L.append("## Best Epoch")
    L.append("")
    bm = _metric_row(best)
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| Epoch # | {bm['epoch']} |")
    L.append(f"| Loss | {_f(bm['loss'], 6)} |")
    L.append(f"| Total trades | {bm['trades'] if bm['trades'] is not None else '—'} |")
    L.append(f"| Profit total | {_pct(bm['profit_total'])} |")
    L.append(f"| Sharpe | {_f(bm['sharpe'], 3)} |")
    L.append(f"| Sortino | {_f(bm['sortino'], 3)} |")
    L.append(f"| Max drawdown | {_pct(bm['mdd'])} |")
    L.append("")

    L.append("### Best Parameters")
    L.append("")
    params_dict = best.get("params_dict") or {}
    if params_dict:
        L.append("```json")
        L.append(json.dumps(params_dict, indent=2, sort_keys=True))
        L.append("```")
    else:
        L.append("_No params_dict in best epoch._")
    L.append("")

    L.append("## Top 5 Epochs (by loss)")
    L.append("")
    L.append("| # | Epoch | Loss | Trades | Profit | Sharpe | MDD |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|")
    for rank, e in enumerate(sorted_epochs, 1):
        m = _metric_row(e)
        L.append(
            f"| {rank} | {m['epoch']} | {_f(m['loss'], 6)} | "
            f"{m['trades'] if m['trades'] is not None else '—'} | "
            f"{_pct(m['profit_total'])} | {_f(m['sharpe'], 3)} | {_pct(m['mdd'])} |"
        )
    L.append("")

    L.append("## How to apply")
    L.append("")
    L.append("```bash")
    L.append("# best 파라미터를 봇에 반영하려면 hyperopt-show를 통해 확정 후 strategy 코드/.json에 주입")
    L.append("docker compose run --rm freqtrade hyperopt-show --best")
    L.append("")
    L.append("# 또는 본 리포트의 'Best Parameters' JSON을 직접 strategy의")
    L.append("# DecimalParameter / IntParameter default 값에 옮겨심기")
    L.append("```")
    L.append("")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L))

    bp_path = Path(args.best_params)
    bp_path.parent.mkdir(parents=True, exist_ok=True)
    bp_path.write_text(json.dumps(params_dict, indent=2, sort_keys=True))

    print()
    print("=== Best ===")
    print(f"Epoch:   {bm['epoch']}")
    print(f"Loss:    {_f(bm['loss'], 6)}")
    print(f"Profit:  {_pct(bm['profit_total'])}")
    print(f"Sharpe:  {_f(bm['sharpe'], 3)}")
    print(f"MDD:     {_pct(bm['mdd'])}")
    print(f"Report:        {out_path}")
    print(f"Best params:   {bp_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
