# Adaptive Regime Strategy Plan

작성일: 2026-05-20  
대상: `KaiBaseStrategy`, FreqAI/Freqtrade dry-run 운영 개선  
목표: 현재 7일 1건 수준의 거래 빈도 문제를 개선하되, **거래 횟수 자체가 아니라 수수료 차감 후 총 기대수익**을 최우선으로 둔다. 좋은 시그널이 없는 날은 0건도 정상이며, 좋은 시그널이 몰리는 날은 5건 이상도 허용한다.

---

## 0. 현재 상태 진단

### 0.1 운영 관측

2026-05-20 기준 dry-run 관측:

| 항목 | 현재 |
|---|---:|
| 운영 기간 | 약 7일 |
| closed trades | 1건 |
| open trades | 0건 |
| 누적 손익 | +0.7691 USDT |
| 거래 페어 | SOL/USDT:USDT |
| 진입 태그 | `freqai_short` |
| 거래 수익률 | +1.54% |
| 계좌 기준 수익률 | +0.08% |

봇/모델 안정성은 확인됐다.

- 컨테이너는 7일 이상 `healthy`
- 5개 페어 모두 FreqAI 학습 완료
- 6시간 주기 재학습 정상
- Telegram daily report 정상화 완료

문제는 안정성이 아니라 **거래 빈도와 표본 수 부족**이다. 7일 1건이면 현재 전략의 성과를 판단할 수 없다.

### 0.2 거래가 적은 구조적 이유

현재 진입 조건은 다음 가드가 모두 동시에 통과해야 한다.

| 가드 | 현재 역할 | 빈도 영향 |
|---|---|---|
| `do_predict == 1` | FreqAI 예측 가능 상태 | 필요 |
| `DI_values < 0.85` | FreqAI outlier/신뢰도 필터 | 강함 |
| `&-s_close > 0.005` 또는 `< -0.005` | 고정 예측 임계값 | 강함 |
| `funding_rate.abs() < 0.001` | 펀딩 과열 회피 | 중간 |
| `funding_minutes_to_next > 5` | 펀딩 직전 회피 | 약함 |
| 5m EMA 정배열/역배열 | 단기 방향성 필터 | 중간 |
| RSI 30~70 | 극단 구간 회피 | 중간 |
| 1h trend filter ON | 상위 추세 일치 | 강함 |
| Static 5개 페어 | 유니버스 제한 | 강함 |
| limit order only | 미체결 가능성 | 중간 |

현재 병목은 대략 세 가지다.

1. **고정 예측 임계값**  
   `0.005`는 모든 페어/레짐/변동성에 동일하게 적용된다. 저변동 장에서는 너무 높고, 고변동 장에서는 충분히 낮을 수 있다.

2. **상위 추세 필터의 이진 차단**  
   `trend_filter_1h=1`은 방향을 강하게 제한한다. 안정성은 높지만 박스장/단기 반등/역추세 스캘프를 거의 죽인다.

3. **페어 유니버스가 좁음**  
   BTC/ETH/SOL/BNB/XRP 5개만 보면 하루 2~3건을 안정적으로 만들기 어렵다. 특히 1h 가드까지 켜져 있으면 신호 풀이 더 작아진다.

---

## 1. 개선 원칙

### 1.0 의사결정 반영

2026-05-20 사용자 운영 기준:

| 항목 | 결정 |
|---|---|
| 거래 빈도 | 하루 2~3건은 목표값일 뿐 강제하지 않음. 좋은 신호가 없으면 0건도 허용, 좋은 신호가 많으면 5건 이상도 허용 |
| 최우선 목표 | 거래 수가 아니라 **수수료 차감 후 총 수익금액/기대값** |
| 페어 확장 | 거래량 순위 기반 확장 허용. 단, 20개 모니터링은 리소스 확인 후 단계 적용 |
| Claude 비용 | 하루 $1~2 선호. 필요 성능에 따라 모델을 다르게 쓰되, 예산 여유가 있어 성능 좋은 모델 우선 |
| 손실 제한 | 일일 -5% 유지. 반복 발생 시 자동 stop + 즉시 보고 |
| live 전환 | 추가 7일 dry-run 후 보고. 자동 live 전환 금지, 사용자 승인 후 수동 전환 |

### 1.0.1 Tokyo 서버 리소스 확인

2026-05-20 현재 Tokyo A1 2 OCPU / 12GB 서버 상태:

| 항목 | 현재 |
|---|---:|
| CPU | 평시 약 5% |
| 메모리 | 1.8GB / 11.6GB |
| available memory | 약 9.3GB |
| swap | 4GB, 사용 0 |
| 디스크 | 11GB / 45GB |
| FreqAI models | 약 875MB |
| OHLCV data | 약 1.5MB |
| load average | 0.08 / 0.02 / 0.01 |

판단:

- **20개 페어 모니터링 자체는 가능**하다.
- 병목은 RAM보다 CPU/학습시간이다. 5개 → 20개 확장 시 FreqAI 재학습 시간이 대략 3~5배 늘 수 있다.
- Claude 호출도 페어 수에 비례해 늘 수 있으므로, 페어 확장 전 `CLAUDE_CACHE_TTL_SECONDS`를 3600~7200으로 늘리거나 레짐/이벤트 기반 호출로 제한해야 한다.
- 권장 적용은 `5개 → 10개 → 20개` 단계 확장이다. 바로 20개로 열기보다 10개에서 48시간 gate metrics를 보고 병목을 확인한다.

### 1.1 목표

거래 빈도 목표는 강제 목표가 아니라 관측 기준이다.

| 기간 | 목표 |
|---|---:|
| 하루 평균 | 0~5건 허용, 품질 좋은 신호만 |
| 7일 | 7~35건 범위 허용 |
| 14일 | 14~70건 범위 허용 |
| live 전환 판단 최소 표본 | 50건 이상 |

성과 목표:

| 항목 | 기준 |
|---|---:|
| closed trade 수 | 최소 50건 |
| profit factor | 1.15 이상 |
| max drawdown | 10% 이하 |
| 평균 손익비 | 수수료 포함 양수 |
| 일일 손실 제한 | -3% ~ -5% |
| Claude 비용 | 목표 $1~2/day, soft cap $10/day 이하 |

### 1.2 핵심 방향

전략을 “항상 같은 필터”에서 “레짐별로 다른 필터”로 바꾼다.

현재:

```text
모든 장세에서 동일 조건:
FreqAI threshold + EMA + RSI + 1h trend + funding
```

개선:

```text
시장 레짐 판별
  → 레짐별 threshold / trend filter / ROI / stop / stake / max trades 조정
  → pair별 상대 강도와 유동성 점수로 후보 우선순위 결정
```

이렇게 해야 거래 수를 기계적으로 맞추지 않고, **좋은 장에서만 공격적으로 늘리고 안 좋은 장에서는 쉬는 구조**가 된다.

---

## 2. 레짐 정의

### 2.1 전역 시장 레짐

BTC와 ETH를 시장 베타 proxy로 사용한다.

필요 피처:

| 피처 | timeframe | 의미 |
|---|---|---|
| BTC 1h EMA20/EMA50 | 1h | 단기 시장 방향 |
| BTC 4h EMA50/EMA200 | 4h | 중기 시장 방향 |
| BTC ADX | 1h | 추세 강도 |
| BTC ATR% | 1h | 변동성 |
| BTC Bollinger width percentile | 1h | squeeze/breakout |
| BTC funding rate | live | 선물 과열 |
| ETH/BTC relative strength | 1h | 알트 위험 선호 |

전역 레짐 후보:

| 레짐 | 조건 예시 | 전략 태도 |
|---|---|---|
| `trend_up` | BTC 1h/4h 상승 + ADX 높음 | 롱 우선, 숏 엄격 |
| `trend_down` | BTC 1h/4h 하락 + ADX 높음 | 숏 우선, 롱 엄격 |
| `range` | ADX 낮음 + BB width 중간 | 양방향 mean-reversion 허용 |
| `squeeze` | BB width 하위 20% | 신호 대기, breakout 준비 |
| `high_vol` | ATR% 상위 80% | stake 축소, 짧은 ROI |
| `risk_off` | BTC 급락 + funding/volume 과열 | 신규 진입 최소화 |

### 2.2 페어별 레짐

전역 레짐과 별개로 페어마다 로컬 레짐을 계산한다.

| 피처 | 의미 |
|---|---|
| pair 5m EMA20/EMA50 | 단기 방향 |
| pair 1h EMA20/EMA50 | 상위 방향 |
| pair ADX 5m/1h | 추세 강도 |
| pair ATR% | stop/ROI 거리 |
| pair volume z-score | 거래 가능성 |
| pair relative strength vs BTC | 알파 후보 |
| FreqAI prediction percentile | 현재 예측값의 상대적 강도 |

페어 점수:

```text
pair_score =
  0.35 * freqai_prediction_score
+ 0.20 * trend_alignment_score
+ 0.15 * volume_score
+ 0.15 * volatility_score
+ 0.10 * relative_strength_score
+ 0.05 * funding_score
```

진입은 단순 threshold가 아니라 `pair_score` 기반으로 후보를 정렬한다.

---

## 3. 진입 구조 개선

### 3.1 고정 threshold → 동적 threshold

현재:

```python
buy_threshold = 0.005
sell_threshold = -0.005
```

문제:

- 저변동 장에서는 신호가 거의 안 나온다.
- 고변동 장에서는 신호가 너무 많거나 늦게 나온다.
- 페어별 변동성 차이를 무시한다.

개선:

```text
dynamic_long_threshold =
  rolling_quantile(prediction, regime_long_quantile)

dynamic_short_threshold =
  rolling_quantile(prediction, regime_short_quantile)
```

레짐별 기본값:

| 레짐 | long quantile | short quantile | 의도 |
|---|---:|---:|---|
| `trend_up` | 0.65 | 0.15 | 롱 쉽게, 숏 어렵게 |
| `trend_down` | 0.85 | 0.35 | 롱 어렵게, 숏 쉽게 |
| `range` | 0.70 | 0.30 | 양방향 허용 |
| `squeeze` | 0.80 | 0.20 | breakout만 |
| `high_vol` | 0.85 | 0.15 | 강한 신호만 |
| `risk_off` | 0.95 | 0.05 | 사실상 차단 |

목표는 “예측값이 절대 0.5% 이상”이 아니라, **해당 페어/레짐에서 상대적으로 상위권 신호인지**를 보는 것이다.

### 3.2 이진 추세 필터 → 점수형 추세 필터

현재:

```text
trend_filter_1h ON이면
  long: trend_up_1h == 1
  short: trend_up_1h == 0
```

개선:

```text
trend_alignment_score:
  strong align: +1.0
  weak align:   +0.5
  neutral:      0.0
  against:     -0.5
```

레짐별 적용:

| 레짐 | 추세 필터 |
|---|---|
| `trend_up/down` | 강하게 적용 |
| `range` | 약하게 적용 |
| `squeeze` | breakout 방향만 적용 |
| `high_vol` | 방향보다 변동성/위험 우선 |
| `risk_off` | 대부분 차단 |

즉, 1h 추세가 반대라는 이유만으로 모든 진입을 죽이지 않고, **더 높은 FreqAI 점수와 더 작은 stake로 제한적 허용**한다.

### 3.3 신호 등급화

모든 진입을 같은 크기로 보지 않는다.

| 등급 | 조건 | stake | 목적 |
|---|---|---:|---|
| A | 레짐/추세/FreqAI 모두 일치 | 1.0x | 핵심 거래 |
| B | FreqAI 강함, 추세 일부 일치 | 0.7x | 빈도 확보 |
| C | range mean-reversion | 0.4x | 소액 탐색 |
| D | risk_off/high_vol 역방향 | 0x | 차단 |

초기 구현에서는 `stake_amount`를 고정으로 두더라도, `enter_tag`를 `regime_A_long`, `regime_B_short`처럼 남겨서 백테스트/리포트에서 등급별 성과를 분리한다.

---

## 4. 페어 유니버스 개선

### 4.1 현재 문제

현재 whitelist:

```text
BTC, ETH, SOL, BNB, XRP
```

5개 페어는 안정적이지만, 하루 2~3건 목표에는 후보 풀이 작다.

### 4.2 단계적 확장

1단계: 정적 8~10개

```text
BTC, ETH, SOL, BNB, XRP,
DOGE, ADA, AVAX, LINK, TON
```

선정 기준:

- Binance USDT-M liquidity 상위권
- 스프레드 낮음
- 급격한 상폐/테마 리스크 낮음
- 5m/15m/1h 데이터 충분

2단계: hybrid pairlist

```text
Static core 5개
+ VolumePairList 상위 5개
+ blacklist/age/liquidity 필터
```

주의:

- FreqAI는 페어별 학습 데이터가 필요하다.
- 동적 페어 변경이 너무 잦으면 모델 준비 전 신호가 빈다.
- 최소 1일 이상 같은 유니버스를 유지하는 hysteresis가 필요하다.

### 4.3 권장 초기 확장

우선은 `StaticPairList`를 10개로 확장한다. 동적 pairlist와 20개 확장은 그 다음 단계.

이유:

- 구현 리스크 낮음
- 백테스트 비교 쉬움
- FreqAI 모델 준비 안정적

20개 확장 조건:

| 조건 | 기준 |
|---|---:|
| 10개 운영 48시간 후 메모리 | 컨테이너 6GB 이하 |
| 재학습 사이클 | 6시간 retrain window 안에 완료 |
| Claude 비용 | $2/day 이하 또는 모델/TTL 조정 완료 |
| gate metrics | 최종 후보가 페어 증가에 비례해 증가 |
| daily report | API/fee/cost 정상 |

20개까지 갈 경우 추천 후보군은 “고정 10개 + 거래량 상위 10개” 혼합이다. 완전 동적 20개는 모델 준비/페어 churn 문제가 있으므로 아직 이르다.

---

## 5. 청산/리스크 구조 개선

### 5.1 ROI를 레짐별로 조정

현재 ROI:

```python
minimal_roi = {
    "0": 0.025,
    "30": 0.015,
    "60": 0.008,
    "120": 0.003,
    "180": 0,
}
```

이 구조는 거래 빈도를 낮출 수 있다. 특히 소폭 예측 신호를 많이 잡으려면 청산도 더 민첩해야 한다.

레짐별 아이디어:

| 레짐 | ROI 성향 |
|---|---|
| `trend` | 더 오래 보유, trailing 중심 |
| `range` | 짧은 ROI, 빠른 익절 |
| `high_vol` | 짧은 ROI + 넓은 stop 또는 stake 축소 |
| `squeeze_breakout` | 초기 넓게, 이후 trailing |

Freqtrade에서는 `custom_roi` 또는 exit signal/tag 기반으로 단계 적용 검토.

### 5.2 ATR 기반 stoploss

현재:

```python
stoploss = -0.02
```

문제:

- 변동성 낮은 페어에는 너무 넓다.
- 변동성 높은 페어에는 너무 좁다.

개선:

```text
stop_distance = ATR% * regime_multiplier
```

예시:

| 레짐 | stop multiplier |
|---|---:|
| range | 1.2 ATR |
| trend | 2.0 ATR |
| high_vol | 1.5 ATR + stake 축소 |
| risk_off | 신규 진입 차단 |

### 5.3 Protections 완화/세분화

현재:

- `StoplossGuard`: 60 candles 내 2 stoploss → 60 candles 정지
- `MaxDrawdown`: 288 candles 내 5 trades, MDD 5% → 144 candles 정지
- `CooldownPeriod`: 3 candles

거래량 확대 초기에는 protection이 자주 걸릴 수 있다. 다만 완화는 조심해야 한다.

권장:

- 전체 protection은 유지
- pair별 cooldown을 짧게
- global max drawdown은 유지
- entry 등급별 손실 통계로 나쁜 등급만 제거

---

## 6. 구현 로드맵

### Phase 1. 측정부터 추가

목표: 거래가 왜 안 나는지 수치로 본다.

작업:

1. `populate_entry_trend`에 가드별 pass/fail 카운터 추가
2. 페어별로 다음 값 daily report에 추가
   - `do_predict` 통과율
   - DI 통과율
   - threshold 통과율
   - 1h trend 통과율
   - EMA/RSI 통과율
   - 최종 진입 후보 수
3. `entry_debug.csv` 또는 `user_data/metrics/entry_gates_YYYY-MM-DD.json` 저장

효과:

- “어떤 가드가 제일 많이 죽이는지”가 보인다.
- 성급한 완화 대신 정확한 병목부터 푼다.

우선순위: **최상**

### Phase 2. Static whitelist 10개 확장

작업:

1. whitelist를 5개 → 10개로 확장
2. `download-data` timeframes 유지
3. 7일 dry-run 비교
4. daily report에 pair별 신호/거래 수 추가

예상 효과:

- 거래 후보 약 2배 증가
- 모델 학습 시간 증가
- Claude 비용 증가 가능

주의:

- `CLAUDE_CACHE_TTL_SECONDS`를 1800 → 3600으로 늘려 비용 증가를 억제할 수 있다.
- LLM sentiment가 현재 상수로 제거되고 있으므로, 페어 확장 전 LLM 호출 빈도를 줄이는 것도 검토.

### Phase 3. 레짐 컬럼 추가

작업:

1. `market_regime` 계산
2. `pair_regime` 계산
3. dataframe에 다음 컬럼 추가
   - `market_regime_id`
   - `pair_regime_id`
   - `volatility_percentile`
   - `prediction_long_quantile`
   - `prediction_short_quantile`
4. plot/debug에 포함

초기 레짐 단순화:

```text
0 = range
1 = trend_up
2 = trend_down
3 = high_vol
4 = risk_off
```

### Phase 4. 동적 threshold 적용

작업:

1. rolling prediction quantile 계산
2. 레짐별 quantile threshold 적용
3. 기존 `buy_threshold/sell_threshold`는 fallback 또는 minimum edge로 유지
4. enter_tag에 레짐/등급 기록

예시 enter_tag:

```text
trend_up_A_long
range_C_short
high_vol_B_long
```

검증:

- 기존 전략 vs 동적 threshold 전략 백테스트
- 목표: 거래 수 3~5배 증가, profit factor 1.1 이상 유지

### Phase 5. 점수형 entry로 전환

작업:

1. `entry_score_long`, `entry_score_short` 계산
2. 레짐별 최소 점수 적용
3. 이진 trend filter 제거 또는 보조 점수화

초기 점수 임계값:

| 레짐 | min score |
|---|---:|
| trend aligned | 0.65 |
| range | 0.70 |
| high_vol | 0.80 |
| risk_off | 0.95 |

### Phase 6. 레짐별 exit/stop

작업:

1. `custom_stoploss` 도입
2. ATR 기반 stop
3. 레짐별 ROI/exit signal 도입
4. exit_reason 분포 리포트 강화

---

## 7. 검증 방법

### 7.1 실험 순서

각 변경은 한 번에 하나씩 적용한다.

1. Baseline: 현재 전략
2. + Pair universe 10개
3. + Regime columns only (진입 영향 없음)
4. + Dynamic threshold
5. + Score entry
6. + Regime exit/stop

각 단계별로:

```bash
./scripts/run_backtest.sh --days 90
./scripts/run_hyperopt.sh --epochs 200
```

그리고 최소 48시간 dry-run으로 실제 신호 빈도 확인.

### 7.2 비교 지표

| 지표 | 목표 |
|---|---:|
| trades/day | 2~3 |
| winrate | 45% 이상이면 충분 |
| profit factor | 1.15 이상 |
| expectancy | 양수 |
| max drawdown | 10% 이하 |
| avg duration | 15~90분 |
| fee / gross profit | 30% 이하 |
| stoploss 비율 | 25% 이하 |

주의:

- winrate보다 expectancy와 profit factor를 우선한다.
- trades/day만 맞추고 fee가 수익을 갉아먹으면 실패다.

---

## 8. 즉시 실행 가능한 1차 개선안

현재 코드 변경 우선순위:

### 8.1 먼저 할 것

1. **entry gate metrics 추가**
   - 지금 어떤 가드가 병목인지 확인
   - 하루만 돌려도 개선 방향이 선명해진다.

2. **whitelist 10개 확장**
   - StaticPairList 유지
   - 하루 2~3건 목표에 가장 직접적

3. **1h trend filter를 hyperopt 결과 기반으로 재평가**
   - 현재 default ON
   - 거래 수가 지나치게 적으면 OFF 또는 score화 후보

4. **threshold를 고정값에서 rolling quantile로 전환**
   - 가장 큰 구조 개선

### 8.2 아직 하지 말 것

1. 바로 `dry_run=false`
2. 무작정 `buy_threshold=0.001`로 낮추기
3. protection 제거
4. max_open_trades 과도 확대
5. 레버리지 상승

거래 수를 늘리는 건 쉽다. 문제는 “나쁜 거래도 같이 늘어나는 것”이다.

---

## 9. 제안 구현 순서

### Sprint 1: 관측과 빈도 회복

작업:

1. `entry_gate_metrics.py` 또는 strategy 내부 카운터
2. daily report에 gate pass rate 추가
3. whitelist 10개 확장
4. LLM 비용 제어: TTL 1~2시간 또는 sentiment 호출 페어 제한

목표:

- trades/day를 억지로 맞추지 않고, 최종 후보/진입 후보가 얼마나 생성되는지 관측
- 어떤 가드가 병목인지 확인

### Sprint 2: 레짐 기반 threshold

작업:

1. market/pair regime 컬럼 추가
2. rolling quantile threshold
3. enter_tag 레짐 기록

목표:

- trades/day 2~3
- profit factor 1.1 이상

### Sprint 3: 레짐별 exit/stop

작업:

1. ATR stop
2. regime ROI
3. score별 stake scaling

목표:

- 거래 수 증가 후 drawdown 억제
- stoploss 비율 25% 이하

---

## 10. 현재 구현 상태

2026-05-20 1차 구현 범위:

1. **entry gate metrics 추가**
   - `KaiBaseStrategy.populate_entry_trend`에서 가드별 pass rate와 최종 후보 수를 페어별 JSON으로 기록한다.
   - 저장 위치: `user_data/metrics/entry_gates_<PAIR>.json`

2. **daily report 연동**
   - Telegram/CLI daily report에 `Entry Gate Metrics` 섹션을 추가한다.
   - 우선 표시 항목은 Base, Long Pred, Short Pred, Long Final, Short Final이다.

3. **Static whitelist 10개 확장**
   - 기존: BTC, ETH, SOL, BNB, XRP
   - 추가: DOGE, ADA, AVAX, LINK, TON
   - 동적 VolumePairList는 모델 churn을 피하기 위해 다음 단계로 보류한다.

4. **운영 방침**
   - 추가 7일 dry-run을 수행한다.
   - 자동 live 전환은 하지 않는다.
   - 7일 후 거래 수, 수수료 차감 손익, gate metrics, Claude 비용을 보고한 뒤 live 전환 여부를 묻는다.

잔여 의사결정:

1. 10개 페어에서 24~48시간 gate metrics를 본 뒤 20개 확장 여부 결정
2. Claude 비용이 $2/day를 넘으면 `CLAUDE_CACHE_TTL_SECONDS` 3600~7200 또는 모델 라우팅 조정
3. gate metrics상 threshold 병목이 확인되면 rolling quantile 기반 동적 threshold 적용

---

## 11. 권장 결론

현재 전략은 안정적이지만 너무 방어적이다.  
가장 좋은 다음 움직임은 곧바로 threshold를 낮추는 것이 아니라:

1. **가드별 pass/fail 측정**
2. **페어 10개 확장**
3. **레짐 컬럼 추가**
4. **동적 threshold**
5. **점수형 entry**

이 순서다.

이렇게 하면 거래 횟수를 억지로 맞추지 않으면서도, 좋은 시그널을 더 많이 잡을 수 있다.  
무턱대고 신호를 열어젖히는 방식은 피해야 한다. 작은 문을 여러 개 만들고, 각 문마다 기록표를 붙이는 방식이 맞다.

추가 dry-run은 최소 7일 수행한다. 7일 후 자동 live 전환은 하지 않는다. 결과를 보고하고, 사용자 확인을 받은 뒤에만 live 전환한다.
