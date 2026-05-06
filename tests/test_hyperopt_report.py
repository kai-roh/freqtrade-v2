"""hyperopt_report: fthypt 파싱, best 선정, 리포트 + best_params.json 생성."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hyperopt_report.py"


def _epoch(loss, profit, sharpe, mdd, trades, params, epoch_num):
    return {
        "loss": loss,
        "current_epoch": epoch_num,
        "params_dict": params,
        "results_metrics": {
            "total_trades": trades,
            "profit_total": profit,
            "sharpe": sharpe,
            "max_drawdown_account": mdd,
        },
    }


def _write_jsonl(path: Path, epochs):
    with open(path, "w") as f:
        for e in epochs:
            f.write(json.dumps(e) + "\n")


def _run(results: Path, out: Path, bp: Path, **extras):
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--results", str(results),
         "--output", str(out),
         "--best-params", str(bp),
         "--strategy", extras.get("strategy", "KaiBaseStrategy"),
         "--epochs", extras.get("epochs", "100"),
         "--loss", extras.get("loss", "SharpeHyperOptLoss"),
         "--timerange", extras.get("timerange", "20260207-20260507")],
        capture_output=True, text=True,
    )


def test_parse_fthypt_loads_module(tmp_path):
    """parse_fthypt를 모듈로 import하여 단위 검증."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    if "hyperopt_report" in sys.modules:
        importlib.reload(sys.modules["hyperopt_report"])
    import hyperopt_report
    p = tmp_path / "r.fthypt"
    p.write_text(
        '\n'.join([
            json.dumps(_epoch(-0.5, 0.1, 1.2, 0.05, 100,
                              {"buy_threshold": 0.005}, 1)),
            "",
            "{not valid json",
            json.dumps(_epoch(-0.7, 0.2, 1.5, 0.04, 120,
                              {"buy_threshold": 0.008}, 2)),
        ])
    )
    epochs = hyperopt_report.parse_fthypt(p)
    assert len(epochs) == 2  # 빈 줄과 잘못된 라인은 스킵
    best = hyperopt_report.best_epoch(epochs)
    assert best is not None
    assert best["current_epoch"] == 2  # loss -0.7이 더 낮음 (좋음)


def test_full_run_produces_report_and_params(tmp_path):
    epochs = [
        _epoch(-0.40, 0.12, 1.10, 0.06, 80,
               {"buy_threshold": 0.004, "sell_threshold": -0.005,
                "di_threshold_buy": 0.80}, 1),
        _epoch(-0.65, 0.18, 1.42, 0.08, 130,
               {"buy_threshold": 0.008, "sell_threshold": -0.007,
                "di_threshold_buy": 0.85}, 2),
        _epoch(-0.50, 0.15, 1.25, 0.07, 110,
               {"buy_threshold": 0.006, "sell_threshold": -0.006,
                "di_threshold_buy": 0.82}, 3),
    ]
    rp = tmp_path / "results.fthypt"
    _write_jsonl(rp, epochs)

    out = tmp_path / "REPORT.md"
    bp = tmp_path / "best_params.json"
    proc = _run(rp, out, bp)
    assert proc.returncode == 0, proc.stderr

    md = out.read_text()
    assert "# Hyperopt Report" in md
    assert "Best Epoch" in md
    assert "Top 5 Epochs" in md
    # epoch 2가 loss 최소 (-0.65) → best
    assert "0.008" in md  # buy_threshold of best
    assert '"di_threshold_buy": 0.85' in md or "di_threshold_buy" in md

    params = json.loads(bp.read_text())
    assert params["buy_threshold"] == 0.008
    assert params["sell_threshold"] == -0.007
    assert params["di_threshold_buy"] == 0.85


def test_missing_file(tmp_path):
    proc = _run(tmp_path / "nope.fthypt", tmp_path / "x.md", tmp_path / "x.json")
    assert proc.returncode == 2


def test_empty_file_returns_2(tmp_path):
    rp = tmp_path / "empty.fthypt"
    rp.write_text("")
    proc = _run(rp, tmp_path / "x.md", tmp_path / "x.json")
    assert proc.returncode == 2


def test_only_invalid_lines_returns_2(tmp_path):
    rp = tmp_path / "garbage.fthypt"
    rp.write_text("not json\n{also not\n")
    proc = _run(rp, tmp_path / "x.md", tmp_path / "x.json")
    assert proc.returncode == 2


def test_top5_truncates_to_5(tmp_path):
    epochs = [_epoch(-0.1 - i * 0.01, 0.1, 1.0, 0.05, 100,
                     {"x": i}, i) for i in range(20)]
    rp = tmp_path / "results.fthypt"
    _write_jsonl(rp, epochs)
    out = tmp_path / "REPORT.md"
    bp = tmp_path / "best_params.json"
    proc = _run(rp, out, bp)
    assert proc.returncode == 0
    md = out.read_text()
    # Top 5 표 → 정확히 5행만 (헤더 + 구분선 + 5)
    top5_section = md.split("## Top 5 Epochs")[1].split("##")[0]
    rows = [ln for ln in top5_section.splitlines()
            if ln.startswith("|") and not ln.startswith("|---") and "|---:" not in ln]
    # 헤더 + 5 데이터행
    assert len(rows) == 6
