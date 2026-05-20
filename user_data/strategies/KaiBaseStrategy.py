"""
KaiBaseStrategy - Freqtrade + FreqAI + Claude 기반 USDT-M Futures 전략

설계 원칙:
1. FreqAI가 가격 예측의 주축 (LightGBM 기반)
2. Claude는 이벤트 트리거 시에만 호출 (비용 통제)
3. 펀딩 비율 기반 진입 차단
4. 메이커 우선 지정가 진입

작성자: Kai Roh
"""

import json
import logging
import sys
import time
from pathlib import Path

# /freqtrade 를 sys.path에 추가해 user_data.llm.* 패키지 import 가능하게.
# Freqtrade가 strategy를 동적 로드할 때 cwd만 잡고 /freqtrade 를 path에 안 넣음.
# parents[2] = .../user_data/strategies → .../user_data → .../  (= /freqtrade in container)
_FT_ROOT = str(Path(__file__).resolve().parents[2])
if _FT_ROOT not in sys.path:
    sys.path.insert(0, _FT_ROOT)

import pandas as pd  # noqa: E402
import talib.abstract as ta  # noqa: E402
from freqtrade.strategy import (  # noqa: E402
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
)
from pandas import DataFrame  # noqa: E402

logger = logging.getLogger(__name__)

# 펀딩 비율 캐시 TTL — Binance 펀딩은 8시간 단위지만 rate는 분 단위로 변동.
# 60초로 설정해 process_throttle_secs(5s)와 충돌 없이 분당 1회 갱신.
_FUNDING_REFRESH_SECONDS = 60
_ENTRY_METRICS_WRITE_SECONDS = 60
_ENTRY_METRICS_DIR = Path("/freqtrade/user_data/metrics")


class KaiBaseStrategy(IStrategy):
    """
    USDT-M Futures 전용. Long/Short 양방향.
    """

    INTERFACE_VERSION = 3

    # === 기본 설정 ===
    timeframe = "5m"
    can_short = True
    use_exit_signal = True
    exit_profit_only = False
    process_only_new_candles = True
    startup_candle_count = 200

    # === 손절/익절 ===
    stoploss = -0.02  # -2% 하드 손절 (자본 대비 -2% 아니라 포지션 대비)
    trailing_stop = True
    trailing_stop_positive = 0.005
    trailing_stop_positive_offset = 0.012
    trailing_only_offset_is_reached = True

    # === ROI (시간 경과별 익절) ===
    minimal_roi = {
        "0": 0.025,  # 즉시 +2.5%면 익절
        "30": 0.015,  # 30분 후 +1.5%
        "60": 0.008,  # 60분 후 +0.8%
        "120": 0.003,  # 120분 후 +0.3%
        "180": 0,  # 180분 후 본전이면 청산
    }

    # === 레버리지 ===
    leverage_value = 5

    # === 하이퍼옵트 가능 파라미터 ===
    buy_threshold = DecimalParameter(0.0, 0.02, default=0.005, space="buy", optimize=True)
    sell_threshold = DecimalParameter(-0.02, 0.0, default=-0.005, space="sell", optimize=True)
    di_threshold_buy = DecimalParameter(0.5, 1.0, default=0.9, space="buy", optimize=True)
    funding_max = DecimalParameter(0.0005, 0.002, default=0.001, space="buy", optimize=False)
    # 다음 펀딩 시점이 이 분(min) 이내면 신규 진입 차단
    funding_blackout_minutes = IntParameter(0, 30, default=5, space="buy", optimize=False)
    # 다중 시간프레임 추세 일치 가드 (D-3). 0=비활성 / 1=활성
    trend_filter_1h = IntParameter(0, 1, default=1, space="buy", optimize=True)
    trend_filter_4h = IntParameter(0, 1, default=0, space="buy", optimize=True)

    # 펀딩 캐시: { pair: {"rate": float, "next_ts_ms": int, "fetched_at": float} }
    _funding_cache: dict = {}
    # entry gate metrics write throttle: {pair: epoch_seconds}
    _entry_metrics_last_write: dict = {}

    # === 보호 로직 (Freqtrade 2026.4+: strategy @property로 정의 필수) ===
    @property
    def protections(self) -> list[dict]:
        return [
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 60,
                "trade_limit": 2,
                "stop_duration_candles": 60,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 288,
                "trade_limit": 5,
                "stop_duration_candles": 144,
                "max_allowed_drawdown": 0.05,
            },
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 3,
            },
        ]

    # === 플롯 설정 ===
    plot_config = {
        "main_plot": {
            "ema_20": {"color": "orange"},
            "ema_50": {"color": "purple"},
        },
        "subplots": {
            "FreqAI Pred": {
                "&-s_close": {"color": "green"},
                "do_predict": {"color": "blue"},
            },
            "RSI": {"rsi": {"color": "red"}},
        },
    }

    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """모든 페어 동일 레버리지 적용"""
        return self.leverage_value

    # ============================================================
    # 다중 시간프레임 (D-3): 1h / 4h 추세 컨텍스트
    # ============================================================
    def informative_pairs(self):
        """페어별 1h, 4h 데이터 추가 fetch."""
        if not self.dp:
            return []
        whitelist = self.dp.current_whitelist()
        return [(p, "1h") for p in whitelist] + [(p, "4h") for p in whitelist]

    def _attach_higher_tf(self, dataframe: DataFrame, pair: str) -> DataFrame:
        """1h/4h 추세 컬럼을 메인 dataframe에 병합. fail-soft."""
        if not self.dp:
            return dataframe

        # 1h: 단기/중기 EMA 정배열 → 상승 추세
        try:
            inf_1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h").copy()
            if not inf_1h.empty and len(inf_1h) >= 50:
                inf_1h["ema_20"] = ta.EMA(inf_1h, timeperiod=20)
                inf_1h["ema_50"] = ta.EMA(inf_1h, timeperiod=50)
                inf_1h["rsi"] = ta.RSI(inf_1h, timeperiod=14)
                inf_1h["trend_up"] = (inf_1h["ema_20"] > inf_1h["ema_50"]).astype(int)
                dataframe = merge_informative_pair(
                    dataframe, inf_1h, self.timeframe, "1h", ffill=True
                )
        except Exception as e:
            logger.warning(f"[mtf] 1h merge failed for {pair}: {e}")

        # 4h: 장기 EMA 정배열 → 거시 추세
        try:
            inf_4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h").copy()
            if not inf_4h.empty and len(inf_4h) >= 200:
                inf_4h["ema_50"] = ta.EMA(inf_4h, timeperiod=50)
                inf_4h["ema_200"] = ta.EMA(inf_4h, timeperiod=200)
                inf_4h["trend_up"] = (inf_4h["ema_50"] > inf_4h["ema_200"]).astype(int)
                dataframe = merge_informative_pair(
                    dataframe, inf_4h, self.timeframe, "4h", ffill=True
                )
        except Exception as e:
            logger.warning(f"[mtf] 4h merge failed for {pair}: {e}")

        return dataframe

    # ============================================================
    # 펀딩 비율 (실시간 조회 + 캐싱)
    # ============================================================
    def bot_loop_start(self, current_time, **kwargs) -> None:
        """
        매 루프 진입 시 호출. 페어별 funding rate를 60초 TTL 캐시로 갱신.
        백테스트에서는 dp.runmode 가 backtest/hyperopt 이므로 스킵 (펀딩 데이터 별도 처리).
        """
        try:
            runmode = self.dp.runmode.value if self.dp else ""
        except Exception:
            runmode = ""
        if runmode in ("backtest", "hyperopt"):
            return

        whitelist = self.dp.current_whitelist() if self.dp else []
        now = time.time()
        for pair in whitelist:
            cached = self._funding_cache.get(pair)
            if cached and (now - cached.get("fetched_at", 0)) < _FUNDING_REFRESH_SECONDS:
                continue
            rate, next_ts = self._fetch_funding_rate(pair)
            if rate is None:
                # 조회 실패 — 기존 캐시 유지, 없으면 0.0으로 보수적 진입 허용
                # (가드 자체는 funding_blackout으로 보강됨)
                if cached is None:
                    self._funding_cache[pair] = {
                        "rate": 0.0,
                        "next_ts_ms": 0,
                        "fetched_at": now,
                    }
                continue
            self._funding_cache[pair] = {
                "rate": float(rate),
                "next_ts_ms": int(next_ts or 0),
                "fetched_at": now,
            }
            logger.debug(f"[funding] {pair} rate={rate:.6f} next={next_ts}")

    def _fetch_funding_rate(self, pair: str):
        """
        ccxt 직접 호출 (freqtrade의 Exchange 래퍼는 funding rate 단건 조회를
        공식 노출하지 않으므로). 실패 시 (None, None) 반환.
        """
        try:
            ccxt_api = self.dp._exchange._api  # noqa: SLF001
            data = ccxt_api.fetch_funding_rate(pair)
            rate = data.get("fundingRate")
            next_ts = data.get("fundingTimestamp") or data.get("nextFundingTimestamp")
            return rate, next_ts
        except Exception as e:
            logger.warning(f"[funding] fetch failed for {pair}: {e}")
            return None, None

    def _funding_blackout(self, pair: str, current_ms: int) -> bool:
        """다음 펀딩 시점이 funding_blackout_minutes 이내면 True (진입 차단)."""
        info = self._funding_cache.get(pair)
        if not info:
            return False
        next_ts = info.get("next_ts_ms", 0)
        if not next_ts:
            return False
        delta_min = (next_ts - current_ms) / 60_000.0
        return 0 <= delta_min <= float(self.funding_blackout_minutes.value)

    # ============================================================
    # FreqAI Feature Engineering
    # ============================================================
    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        """
        FreqAI가 모든 timeframe / corr_pair 조합에 자동 적용하는 피처
        period는 config의 indicator_periods_candles에서 자동 주입
        """
        dataframe[f"%-rsi-period_{period}"] = ta.RSI(dataframe, timeperiod=period)
        dataframe[f"%-mfi-period_{period}"] = ta.MFI(dataframe, timeperiod=period)
        dataframe[f"%-adx-period_{period}"] = ta.ADX(dataframe, timeperiod=period)
        dataframe[f"%-sma-period_{period}"] = ta.SMA(dataframe, timeperiod=period)
        dataframe[f"%-ema-period_{period}"] = ta.EMA(dataframe, timeperiod=period)

        bollinger = ta.BBANDS(dataframe, timeperiod=period, nbdevup=2.0, nbdevdn=2.0)
        dataframe[f"%-bb_lowerband-period_{period}"] = bollinger["lowerband"]
        dataframe[f"%-bb_middleband-period_{period}"] = bollinger["middleband"]
        dataframe[f"%-bb_upperband-period_{period}"] = bollinger["upperband"]
        dataframe[f"%-bb_width-period_{period}"] = (
            bollinger["upperband"] - bollinger["lowerband"]
        ) / bollinger["middleband"]
        dataframe[f"%-close-bb_lower-period_{period}"] = dataframe["close"] / bollinger["lowerband"]

        dataframe[f"%-roc-period_{period}"] = ta.ROC(dataframe, timeperiod=period)

        dataframe[f"%-relative_volume-period_{period}"] = (
            dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        )
        return dataframe

    def feature_engineering_expand_basic(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """period 무관 피처"""
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-raw_volume"] = dataframe["volume"]
        dataframe["%-raw_price"] = dataframe["close"]
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """주 timeframe에서만 한 번 추가"""
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour

        # Claude LLM 피처 (캐시 기반, 비용 통제)
        # 실제 호출은 llm/claude_client.py에서 수행
        # 여기서는 캐시된 값을 읽기만 함
        try:
            from user_data.llm.claude_client import get_cached_sentiment

            pair = metadata.get("pair", "")
            sentiment = get_cached_sentiment(pair)
            dataframe["%-llm_sentiment"] = sentiment
        except Exception as e:
            logger.warning(f"LLM feature skipped: {e}")
            dataframe["%-llm_sentiment"] = 0.0

        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """예측 대상: label_period_candles 후 가격 변화율"""
        dataframe["&-s_close"] = (
            dataframe["close"]
            .shift(-self.freqai_info["feature_parameters"]["label_period_candles"])
            .rolling(self.freqai_info["feature_parameters"]["label_period_candles"])
            .mean()
            / dataframe["close"]
            - 1
        )
        return dataframe

    # ============================================================
    # 일반 인디케이터 (시그널용)
    # ============================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # FreqAI 호출 (피처 + 예측 컬럼 자동 추가됨)
        dataframe = self.freqai.start(dataframe, metadata, self)

        # 추가 시그널용 인디케이터
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # 다중 시간프레임 추세 (D-3): trend_up_1h, trend_up_4h 컬럼 자동 추가
        pair = metadata.get("pair", "")
        if pair:
            dataframe = self._attach_higher_tf(dataframe, pair)

        # 펀딩 비율: bot_loop_start가 채운 캐시에서 읽음.
        # 실시간 캐시 → 마지막 캔들에 brodcast (백테스트는 0.0 유지).
        info = self._funding_cache.get(pair) or {}
        dataframe["funding_rate"] = float(info.get("rate", 0.0))
        # 다음 펀딩까지 남은 분 (없으면 큰 값으로 — 가드 무력화)
        next_ts = int(info.get("next_ts_ms", 0))
        if next_ts > 0:
            now_ms = int(time.time() * 1000)
            dataframe["funding_minutes_to_next"] = max(0.0, (next_ts - now_ms) / 60_000.0)
        else:
            dataframe["funding_minutes_to_next"] = 9999.0

        return dataframe

    # ============================================================
    # 진입 시그널
    # ============================================================
    def _record_entry_gate_metrics(
        self,
        dataframe: DataFrame,
        metadata: dict,
        masks: dict[str, pd.Series],
        window: int = 288,
    ) -> None:
        """
        진입 후보가 어느 가드에서 탈락하는지 페어별 JSON으로 기록.
        5m 기준 288 candles ~= 24h. 전략 동작에는 영향 없는 관측 레이어.
        """
        pair = metadata.get("pair", "unknown")
        now = time.time()
        last = self._entry_metrics_last_write.get(pair, 0)
        if now - last < _ENTRY_METRICS_WRITE_SECONDS:
            return
        self._entry_metrics_last_write[pair] = now

        if dataframe.empty:
            return

        tail_index = dataframe.tail(window).index
        total = len(tail_index)
        if total <= 0:
            return

        summary: dict = {
            "pair": pair,
            "timeframe": self.timeframe,
            "timestamp": int(now),
            "window_candles": total,
            "parameters": {
                "buy_threshold": float(self.buy_threshold.value),
                "sell_threshold": float(self.sell_threshold.value),
                "di_threshold_buy": float(self.di_threshold_buy.value),
                "funding_max": float(self.funding_max.value),
                "funding_blackout_minutes": int(self.funding_blackout_minutes.value),
                "trend_filter_1h": int(self.trend_filter_1h.value),
                "trend_filter_4h": int(self.trend_filter_4h.value),
            },
            "gates": {},
        }

        for name, mask in masks.items():
            try:
                aligned = mask.reindex(tail_index).fillna(False).astype(bool)
                passed = int(aligned.sum())
                summary["gates"][name] = {
                    "pass": passed,
                    "rate": round(passed / total, 6),
                }
            except Exception as e:
                summary["gates"][name] = {"error": str(e)}

        try:
            if "&-s_close" in dataframe.columns:
                pred = dataframe["&-s_close"].reindex(tail_index).dropna()
                if not pred.empty:
                    summary["prediction"] = {
                        "min": round(float(pred.min()), 6),
                        "p25": round(float(pred.quantile(0.25)), 6),
                        "p50": round(float(pred.quantile(0.50)), 6),
                        "p75": round(float(pred.quantile(0.75)), 6),
                        "max": round(float(pred.max()), 6),
                    }
        except Exception as e:
            summary["prediction"] = {"error": str(e)}

        try:
            _ENTRY_METRICS_DIR.mkdir(parents=True, exist_ok=True)
            safe_pair = pair.replace("/", "_").replace(":", "_")
            path = _ENTRY_METRICS_DIR / f"entry_gates_{safe_pair}.json"
            path.write_text(json.dumps(summary, indent=2, sort_keys=True))
        except Exception as e:
            logger.warning(f"[entry-metrics] write failed for {pair}: {e}")

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 공통 가드: FreqAI 신뢰도 + 펀딩 비율 절대값 + 펀딩 직전 차단
        blackout_min = float(self.funding_blackout_minutes.value)
        volume_ok = dataframe["volume"] > 0
        do_predict_ok = dataframe["do_predict"] == 1
        di_ok = dataframe["DI_values"] < self.di_threshold_buy.value
        funding_rate_ok = dataframe["funding_rate"].abs() < self.funding_max.value
        funding_blackout_ok = dataframe["funding_minutes_to_next"] > blackout_min
        guard_base = do_predict_ok & di_ok & funding_rate_ok & funding_blackout_ok & volume_ok

        # 다중 시간프레임 추세 가드 (D-3). informative 미존재/NaN은 1(추세 OK)로 폴백.
        ones = pd.Series(1, index=dataframe.index)
        trend_up_1h = (
            dataframe.get("trend_up_1h", ones).fillna(1).astype(int)
            if int(self.trend_filter_1h.value)
            else ones
        )
        trend_up_4h = (
            dataframe.get("trend_up_4h", ones).fillna(1).astype(int)
            if int(self.trend_filter_4h.value)
            else ones
        )
        long_trend_ok = (trend_up_1h == 1) & (trend_up_4h == 1)
        short_trend_ok = (trend_up_1h == 0) & (trend_up_4h == 0)
        # 4h 가드가 꺼졌으면 short 쪽도 4h 일치 요구 안 함
        if not int(self.trend_filter_4h.value):
            short_trend_ok = trend_up_1h == 0
        if not int(self.trend_filter_1h.value):
            long_trend_ok = ones.astype(bool)
            short_trend_ok = ones.astype(bool)

        long_prediction_ok = dataframe["&-s_close"] > self.buy_threshold.value
        short_prediction_ok = dataframe["&-s_close"] < self.sell_threshold.value
        long_ema_ok = dataframe["ema_20"] > dataframe["ema_50"]
        short_ema_ok = dataframe["ema_20"] < dataframe["ema_50"]
        rsi_band_ok = (dataframe["rsi"] > 30) & (dataframe["rsi"] < 70)
        final_long = guard_base & long_prediction_ok & long_ema_ok & rsi_band_ok & long_trend_ok
        final_short = guard_base & short_prediction_ok & short_ema_ok & rsi_band_ok & short_trend_ok

        self._record_entry_gate_metrics(
            dataframe,
            metadata,
            {
                "volume_ok": volume_ok,
                "do_predict_ok": do_predict_ok,
                "di_ok": di_ok,
                "funding_rate_ok": funding_rate_ok,
                "funding_blackout_ok": funding_blackout_ok,
                "guard_base": guard_base,
                "long_prediction_ok": long_prediction_ok,
                "short_prediction_ok": short_prediction_ok,
                "long_ema_ok": long_ema_ok,
                "short_ema_ok": short_ema_ok,
                "rsi_band_ok": rsi_band_ok,
                "long_trend_ok": long_trend_ok,
                "short_trend_ok": short_trend_ok,
                "final_long": final_long,
                "final_short": final_short,
            },
        )

        # 롱 진입: 예측값 > 임계 + EMA 정배열 + RSI 과매도 회복 + MTF 상승 추세
        dataframe.loc[
            final_long,
            ["enter_long", "enter_tag"],
        ] = (1, "freqai_long")

        # 숏 진입: 예측값 < 임계 + EMA 역배열 + RSI 과매수 회복 + MTF 하락 추세
        dataframe.loc[
            final_short,
            ["enter_short", "enter_tag"],
        ] = (1, "freqai_short")

        return dataframe

    # ============================================================
    # 청산 시그널
    # ============================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 롱 청산: 예측 반전 또는 RSI 과매수
        dataframe.loc[
            ((dataframe["&-s_close"] < 0) | (dataframe["rsi"] > 78)) & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "freqai_exit_long")

        # 숏 청산: 예측 반전 또는 RSI 과매도
        dataframe.loc[
            ((dataframe["&-s_close"] > 0) | (dataframe["rsi"] < 22)) & (dataframe["volume"] > 0),
            ["exit_short", "exit_tag"],
        ] = (1, "freqai_exit_short")

        return dataframe

    # ============================================================
    # 커스텀 진입 가격 (메이커 우선)
    # ============================================================
    def custom_entry_price(
        self,
        pair: str,
        trade,
        current_time,
        proposed_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """
        호가창 첫 단가에서 약간 양보해 메이커로 체결 유도
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if dataframe.empty:
            return proposed_rate

        last_close = dataframe["close"].iloc[-1]
        atr = dataframe["atr"].iloc[-1] if "atr" in dataframe.columns else last_close * 0.001

        # 진입 방향에 따라 약간 보수적인 가격 제시
        if side == "long":
            return last_close - atr * 0.1
        else:
            return last_close + atr * 0.1

    # ============================================================
    # 커스텀 손절 (ATR 기반 동적 조정)
    # ============================================================
    def custom_stoploss(
        self, pair: str, trade, current_time, current_rate: float, current_profit: float, **kwargs
    ) -> float:
        """
        +1% 이익 도달 시 본전 + 0.2%로 손절선 끌어올림
        """
        if current_profit > 0.01:
            return -0.005  # 손절선을 진입가 + 0.5%로
        return self.stoploss
