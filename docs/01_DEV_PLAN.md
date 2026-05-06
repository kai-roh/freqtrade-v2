# Kai Freqtrade Kit - 개발 계획 (Development Plan)

작성일: 2026-05-07
대상 저장소: `~/freqtrade-v2`
선행 문서: [00_GUIDE.md](./00_GUIDE.md) (운영 셋업 가이드)

> 본 문서는 **운영 가이드(00_GUIDE.md)와 별개**로, 키트 자체를 개선·확장하기 위한 개발 로드맵입니다.
> 운영 시 단계(Phase 0~7)는 가이드를 따르고, 본 문서는 코드/인프라 작업 항목을 다룹니다.

---

## 0. 프로젝트 현황 요약

### 0.1 구성 요소
| 영역 | 파일 | 상태 |
|---|---|---|
| 컨테이너 | `docker-compose.yml` | ✅ freqai 이미지 기반 정상 |
| 환경변수 | `.env.example` | ✅ Binance / Anthropic / WebUI 키 정의 |
| 메인 설정 | `user_data/config.json` | ✅ 선물 격리 / FreqAI / 보호로직 포함 |
| 전략 | `user_data/strategies/KaiBaseStrategy.py` | 🟡 동작하나 TODO 잔존 |
| FreqAI 모델 | `user_data/freqaimodels/LLMEnhancedModel.py` | 🟡 단일 모델, 골격만 |
| LLM 클라이언트 | `user_data/llm/claude_client.py` | 🟡 골격만 (실데이터 주입 X) |
| 운영 스크립트 | `scripts/control.sh`, `scripts/fee_reconciliation.py` | ✅ |
| 테스트 | (없음) | ❌ |
| CI/CD | (없음) | ❌ |
| 모니터링/알림 | Telegram 비활성, 외부 메트릭 없음 | ❌ |

### 0.2 기술 스택
- **Freqtrade** `stable_freqai` 이미지 (LightGBM/CatBoost/PyTorch 포함)
- **Binance USDT-M Perpetual** (격리 마진, 5x 레버리지)
- **FreqAI**: 5m / 15m / 1h 멀티 타임프레임, BTC·ETH 코릴레이션 페어
- **Claude API**: Opus 4.7 (`claude-opus-4-7`) — 감성 점수 + 이벤트 트리거
- **로컬 단일 호스트**(macOS) Docker Compose 단독 운용

### 0.3 코드 내 명시적 TODO
| 위치 | 내용 | 영향도 |
|---|---|---|
| `KaiBaseStrategy.populate_indicators:176` | `funding_rate = 0.0` (실제 조회 미구현) | 🔴 진입 가드가 사실상 비활성 |
| `claude_client._fetch_sentiment` | 뉴스/SNS 본문 미주입 → 일반 컨텍스트만으로 호출 | 🟠 LLM 피처 신호값이 약함 |
| `claude_client.event_triggered_call` | 트리거 호출 함수만 존재, 호출 지점 없음 | 🟠 비용은 안 들지만 기능 미완 |
| `LLMEnhancedModel.fit` | 단일 LightGBM 회귀, 앙상블 미구현 | 🟡 정확도 개선 여지 |

---

## 1. 개발 목표

### 1.1 1차 목표 (MVP 안정화, 2주)
**dry-run으로 1주 무사 가동 + 모든 TODO 해소.**
- 펀딩 비율 실제 조회
- LLM에 실제 뉴스 컨텍스트 주입(또는 명시적 비활성화 토글)
- 백테스트/하이퍼옵트 자동 실행 스크립트
- 일일/주간 리포트 자동 생성

### 1.2 2차 목표 (라이브 전환, 4주)
- 소액($30~50) 라이브 가동, fee_reconciliation 결과 ±5% 이내 유지
- 프로텍션 룰 모두 트리거 검증 완료
- Telegram 또는 webhook 알림 통합

### 1.3 3차 목표 (성능 개선, 8주+)
- 앙상블 모델(LightGBM + CatBoost) 도입
- 동적 페어 선정 (VolumePairList) 검증 후 적용
- Claude 호출을 이벤트 기반으로 전환해 비용 50% 절감

---

## 2. 단계별 작업 항목 (백로그)

### Phase A. 코어 기능 완성 (1주차)

#### A-1. 펀딩 비율 실시간 조회 — `KaiBaseStrategy`
**문제**: `dataframe["funding_rate"] = 0.0` 으로 박혀 있어 `funding_max` 가드가 무력.
**작업**:
1. `self.dp.get_pair_dataframe(pair, "funding_rate")` 또는 ccxt `fetch_funding_rate` 통합
2. 캔들 시점에 가까운 펀딩 비율을 캐싱하여 broadcast
3. 펀딩 직전(예: 1~5분 전)은 신규 진입 자체 차단하는 `funding_blackout_minutes` 파라미터 추가

**검증**: dry-run에서 `funding_rate` 값이 0이 아닌 실제 값으로 로깅되는지 확인.

#### A-2. Claude LLM 컨텍스트 주입 — `claude_client.py`
**문제**: 뉴스 없이 LLM에 빈 컨텍스트로 호출 → 의미 있는 시그널 X.
**작업**:
1. 뉴스 소스 추상화 인터페이스 (`fetch_recent_news(pair) -> list[str]`)
2. 무료 1차 후보:
   - CryptoPanic API (free tier)
   - Binance announcement RSS
   - X/Twitter 검색은 스킵(API 비용 큼)
3. 폴백 모드 (`LLM_NEWS_PROVIDER=none`) — 키트 그대로도 동작하도록
4. 환경변수: `CRYPTOPANIC_TOKEN`, `LLM_NEWS_PROVIDER`

**검증**: 캐시 갱신 직후 로그에 뉴스 헤드라인 N건 + sentiment 값 함께 출력.

#### A-3. JSON 호환 토글 — `claude_client.py`
**문제**: 응답 파싱 실패 시 무조건 0.0으로 떨어져 디버깅 어려움.
**작업**:
1. Anthropic SDK의 `response_format` 또는 시스템 프롬프트로 JSON-only 강제
2. 파싱 실패 시 raw response 별도 디스크 로깅(`user_data/llm/cache/_failures/`)
3. 실패율이 일정 이상이면 sentinel 값 반환 + 알림

#### A-4. CLAUDE_MODEL 식별자 검증
**문제**: `.env.example`이 `claude-opus-4-7`을 기본으로 사용. 실제 SDK 호출 시 모델 식별자 정확성 확인 필요.
**작업**:
1. `claude-opus-4-7` 식별자 검증 후 필요 시 정정
2. 모델 스위치를 위한 `CLAUDE_MODEL_FALLBACK` (e.g., haiku) 추가 — Opus 비용 폭주 시 자동 강등

> Why: 비용 폭주 한 번이면 일주일치 수익을 상쇄. **요청당 비용 상한**을 코드 단에서 강제할 것.

---

### Phase B. 자동화 & 검증 (2주차)

#### B-1. 백테스트 자동화 — `scripts/run_backtest.sh` ✅
```bash
# 매주 일요일 1회 실행 권장
./scripts/run_backtest.sh --days 90 --pairs BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT
```
**완료 (2026-05-07)**:
1. ✅ `download-data` + `backtesting` 일괄 실행 (skip-download 옵션 지원)
2. ✅ 결과를 `user_data/backtest_results/<UTC-ts>/`로 정리 (`result.json`, `REPORT.md`)
3. ✅ 헤드라인 메트릭 + 페어/태그/exit_reason 분석 마크다운
4. ✅ Live-Gate(Sharpe≥1.0 & MDD≤15%) 판정 → exit code 0=PASS / 1=FAIL
5. ✅ macOS BSD `date` / Linux GNU `date` 모두 지원

#### B-2. 하이퍼옵트 자동화 — `scripts/run_hyperopt.sh` ✅
**완료 (2026-05-07)**:
1. ✅ `scripts/run_hyperopt.sh` — `download-data` + `hyperopt` + 결과 캡처(timestamp dir로 mv) + 리포트. 기본 `SharpeHyperOptLoss`, 100 epochs, random-state 42
2. ✅ `scripts/hyperopt_report.py` — `.fthypt` (JSON Lines) 파싱 → loss 최소 epoch 식별, Top 5 표, `best_params.json` 추출
3. ✅ Exit code: 0=ok, 2=결과 없음/입력 오류, 3=hyperopt 실패
4. ✅ 단위 테스트 6개 추가 (80/80 PASS) — synthetic fthypt fixture
5. ✅ CI 미실행 (수십 분~수 시간 소요) — 리포트 스크립트만 검증

**적용 워크플로**:
```bash
./scripts/run_hyperopt.sh --epochs 200
# → REPORT.md에서 best_params 확인
# → strategy의 DecimalParameter default에 옮겨심기
# → ./scripts/run_backtest.sh --skip-download 으로 walk-forward 검증
```

#### B-3. 데이터 워크플로 정리
**작업**:
1. `download-data`를 `cron`(macOS launchd) 또는 GitHub Actions로 매일 새벽 3시(KST) 실행
2. 90일 롤링 윈도 유지(오래된 데이터 자동 prune)

#### B-4. fee_reconciliation 자동 실행 ✅
**완료 (2026-05-07)**:
1. ✅ `fee_reconciliation.py` 리팩터 — `compute_reconciliation()` import 가능, `--json` 출력 지원
2. ✅ `scripts/daily_report.py` 신설 — Freqtrade API(`/profit`,`/status`,`/balance`,`/daily`) + fee 대사 + Claude 비용 누적 통합 마크다운
3. ✅ 알림 임계: 일일 손실 5%↑ / fee 차이 5%↑ / Claude 하드캡 도달 → exit 1
4. ✅ Freqtrade API 접속 실패 → exit 2 (봇 다운 시그널)
5. ✅ `control.sh daily_report`를 새 스크립트로 위임 (`docker compose exec` 우선, 미실행 시 ephemeral run)
6. ✅ 리포트 자동 저장: `user_data/daily_reports/YYYY-MM-DD.md`

---

### Phase C. 운영 가시성 (3주차)

#### C-1. Telegram 알림 통합 ✅
**완료 (2026-05-07)**:
1. ✅ `scripts/telegram_notify.py` — Bot API 헬퍼 (urllib만, 4096자 자동 분할, fail-soft, CLI/library 양 모드)
2. ✅ `daily_report.py --telegram` / `--telegram-always` — 알림 발생 시 (또는 always) 푸시. 알림 없으면 silent push, 있으면 sound on
3. ✅ `config.json` telegram 섹션 보강: `notification_settings` 화이트리스트(진입/청산/protection/startup만 ON, 취소·캔들·strategy_msg OFF)
4. ✅ `enabled: false` 유지 — placeholder 토큰으로 켜면 봇 자체가 안 뜨므로, 실제 토큰 검증 후 사용자가 수동 토글
5. ✅ 단위 테스트 13개 추가 (74/74 PASS)

**사용 예**:
```bash
# 알림 발생 시만 푸시 (cron 권장)
./scripts/control.sh daily_report -- --telegram

# 매일 결과 + 정상 시 silent 푸시
./scripts/control.sh daily_report -- --telegram-always
```

#### C-2. 메트릭 노출 (옵션)
**작업**:
1. Freqtrade의 `/api/v1/stats`, `/profit`, `/balance`를 가져오는 collector 작성
2. JSON 파일 또는 Prometheus exporter 한 가지 선택
3. 로컬 단독 운용이라 굳이 도입 안 해도 무방 — **메모만 남기고 결정 보류**

#### C-3. 로그 회전(rotation)
**작업**:
1. `freqtrade.log` 일별 회전 + 30일 보관 (logrotate 또는 `--logfile-rotation`)

---

### Phase D. 모델/전략 강화 (4주차 이후)

#### D-1. LightGBM + CatBoost 앙상블 — `LLMEnhancedModel`
**작업**:
1. `LightGBMRegressor`와 `CatboostRegressor`를 동시 fit
2. 평균 또는 보팅 기반 결합
3. backtest 비교: 단일 vs 앙상블 (Sharpe 0.1 이상 개선되면 채택)

#### D-2. 이벤트 트리거 LLM 호출 통합
**작업**:
1. ATR 급증·청산 클러스터·펀딩 급변 등 트리거 정의
2. `KaiBaseStrategy.bot_loop_start` 또는 `confirm_trade_entry` 훅에서 `event_triggered_call` 호출
3. 호출 빈도 상한: **시간당 N회**, 일일 비용 상한 환경변수로 제어

#### D-3. 다중 시간프레임 결합
**작업**:
1. `informative_pairs()` 활성화 — 1h, 4h 추세 컨텍스트
2. 5m 진입 시 1h 추세 일치 강제 (롱은 1h EMA 정배열일 때만)

#### D-4. 동적 페어 선정 (VolumePairList)
**작업**:
1. `pairlists`에 `VolumePairList` 추가 (24h 거래량 기준 상위 N개)
2. 단, 갑작스런 페어 변경은 학습 데이터 부족 야기 → **1시간 단위 hysteresis** 적용
3. 검증 후에만 활성화 (Phase 5 라이브 결과 양호 시)

---

### Phase E. 안정성 & 품질 (지속)

#### E-1. 단위 테스트 ✅
**완료 (2026-05-07)** — 61개 테스트, 호스트 Python(stdlib + pytest)에서 0.5s에 실행. 외부 API 전부 mock.

| 모듈 | 테스트 수 | 커버 |
|---|---:|---|
| `cost_tracker` | 12 | 가격, 캡, 폴백, 디스크 영속, 일자 롤오버, 손상 복구 |
| `news_sources` | 12 | 페어 매핑, 캐시 hit/miss/expiry, CryptoPanic mock, 폴백 |
| `claude_client` | 14 | JSON 파싱(코드펜스/꼬리잡음/엉터리), 캐시, 신뢰도 damping, 이벤트 호출, 뉴스 주입 검증 |
| `fee_reconciliation` | 7 | sqlite fixture, tolerance ok/exceeded, open trade skip, --json |
| `backtest_report` | 7 | PASS/FAIL gate, 누락/손상 입력, 페어 percent 스케일 자동 감지 |
| `daily_report` | 8 | 알림 분기 4종, 빈 입력, open trades 렌더, _pct 헬퍼 |

**부수 발견 — 진짜 버그 수정**: `_strict_parse`의 `split("```", 2)[-1]`이 trailing fence로 빈 문자열을 잡던 버그를 테스트가 잡아 fix.

**보류**: `KaiBaseStrategy` 단위 테스트는 freqtrade/talib 의존성으로 별도 Docker harness 필요. 통합 테스트로 분리 예정.

#### E-2. 코드 품질 ✅
**완료 (2026-05-07)**:
1. ✅ `pyproject.toml` — ruff + black 통합 설정 (line-length 100 — 한글 주석 친화)
2. ✅ ruff 룰셋: E/F/W/I/UP/B (E501은 black이 처리하므로 제외)
3. ✅ `requirements-dev.txt`에 `ruff>=0.6`, `black>=24.0` 추가
4. ✅ 기존 코드 자동 수정: ruff 57건 + 수동 5건 + black 19파일 포맷 — 한 커밋에 묶어 review 친화
5. ✅ CI `lint` job 확장: `ruff check` + `black --check` + `bash -n`
6. ✅ 자동 적용된 모던화: `Optional[X]` → `X | None`, `timezone.utc` → `UTC`, isort, unused import 정리, redundant `r` mode 등

**보류**: `mypy --strict` — strategy 파일은 freqtrade 시그니처 제약 때문에 깊은 작업 필요. 별도 작업으로 분리.

#### E-3. CI (GitHub Actions) ✅
**완료 (2026-05-07)**:
1. ✅ `.github/workflows/ci.yml` — push/PR 트리거, Python 3.11/3.12 매트릭스 pytest + py_compile + `bash -n` 린트, concurrency 취소
2. ✅ `.github/workflows/secrets.yml` — gitleaks 시크릿 스캔 (push/PR + 매주 월 06:00 UTC cron)
3. ✅ `.gitleaks.toml` — `.env.example` placeholder false positive 제외
4. ✅ README CI/Secrets 배지

**보류**: freqtrade backtesting 스모크는 freqai 이미지(>2GB) pull 시간 비용 큼. 별도 매뉴얼/스케줄 워크플로로 분리 예정.

#### E-4. 시크릿 관리
- `.env`는 절대 커밋 금지 → `.gitignore` 보강 필요(현재 미확인, 점검)
- API 키 회전 절차 문서화

---

## 3. 우선순위 매트릭스

| 우선순위 | 작업 | 이유 |
|---|---|---|
| 🔴 P0 | A-1 펀딩 비율, A-3 JSON 강제, A-4 모델 식별자/비용 상한 | 운영 시 직접 손실/비용 직결 |
| 🟠 P1 | A-2 뉴스 주입, B-1 백테스트 자동화, B-4 fee 알림, E-4 시크릿 보호 | 신호 품질·보안 |
| 🟡 P2 | B-2 하이퍼옵트, C-1 텔레그램, D-2 이벤트 트리거 | 성능 개선 |
| 🟢 P3 | D-1 앙상블, D-3 MTF, D-4 VolumePairList, C-2 메트릭 | 검증 후 단계 도입 |

---

## 4. 검증/배포 게이트

각 단계 완료 → 다음 단계 착수 조건:

- **A → B**: dry-run 72시간 무중단 + 시그널 5건 이상 정상 발생
- **B → C**: 백테스트 90일 결과 Sharpe ≥ 1.0 & MDD ≤ 15%
- **C → D**: 라이브 2주 누적 수익률 ≥ 0% + fee 차이 ±5% 이내
- **D 단계 변경**: 기존 단일 모델 대비 walk-forward Sharpe +0.1 이상 개선

게이트 미달 시 다음 단계 진행 금지. 운영자(Kai)가 명시적으로 결정.

---

## 5. 리스크 레지스터

| 리스크 | 가능성 | 영향 | 대응 |
|---|---|---|---|
| Claude API 비용 폭주 | 중 | 중 | 모델 폴백, 일일 호출 상한, 캐시 TTL 상향 |
| Binance API rate limit | 중 | 중 | ccxt `enableRateLimit`, 페어 수 5개 이하 유지 |
| FreqAI 모델 학습 실패 | 낮 | 높 | `purge_old_models: 2`로 마지막 정상 모델 보존, 학습 실패 시 알림 |
| 펀딩 시점 강제 청산 | 중 | 높 | A-1 완료 + Phase 6 funding_blackout 적용 |
| Docker 컨테이너 메모리 OOM | 낮 | 중 | macOS Docker 메모리 6GB+ 할당 권장 |
| `.env` 유출 | 낮 | 매우 높 | `.gitignore` + gitleaks + Withdraw 권한 OFF |
| 백테스트-라이브 괴리 | 중 | 중 | fee_reconciliation 일일 실행, fee 1.5x 보수화 유지 |

---

## 6. 산출물 / 파일 변경 계획

```
docs/
├── 00_GUIDE.md                    # (기존)
├── 01_DEV_PLAN.md                 # (본 문서)
└── 02_RUNBOOK.md                  # TODO: 장애 대응 / 복구 절차
scripts/
├── control.sh                     # (기존)
├── fee_reconciliation.py          # (기존)
├── run_backtest.sh                # NEW (B-1)
├── run_hyperopt.sh                # NEW (B-2)
└── daily_report.py                # NEW (B-4 + C 통합)
user_data/
├── llm/
│   ├── claude_client.py           # 수정 (A-2, A-3, A-4)
│   └── news_sources.py            # NEW (A-2)
├── strategies/
│   └── KaiBaseStrategy.py         # 수정 (A-1, D-3)
└── freqaimodels/
    ├── LLMEnhancedModel.py        # (기존)
    └── EnsembleModel.py           # NEW (D-1)
tests/
├── test_claude_client.py          # NEW (E-1)
├── test_strategy_signals.py       # NEW (E-1)
└── test_fee_reconciliation.py     # NEW (E-1)
.gitignore                         # 점검 (E-4)
```

---

## 7. 타임라인 (제안)

| 주차 | 작업 | 게이트 |
|---|---|---|
| W1 | Phase A 전체 + E-4 시크릿 점검 | dry-run 시작 |
| W2 | Phase B (자동화) + E-1 테스트 (claude_client) | 백테스트 결과 게이트 통과 |
| W3 | Phase C 일부 + 소액 라이브 전환 | 라이브 1주 |
| W4 | 라이브 모니터링 + B-4 fee 보정 | 라이브 2주 누적 결과 |
| W5~W8 | Phase D 단계별 도입(검증→채택) | 게이트별 채택/롤백 결정 |
| 지속 | E (테스트/CI/품질) | — |

---

## 8. 작업 시작 체크리스트

- [ ] `git init` 후 `.gitignore` 점검 (`.env`, `user_data/logs/`, `user_data/models/`, `user_data/backtest_results/` 제외)
- [ ] Phase A 작업 브랜치 생성 (`feat/funding-rate`, `feat/llm-news` 등)
- [ ] `pyproject.toml` 또는 `requirements-dev.txt`에 `pytest`, `ruff`, `black`, `mypy` 추가
- [ ] 본 문서에 매주 진행 상황을 갱신(체크박스 + 짧은 메모)
