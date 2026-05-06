# Freqtrade + FreqAI + Claude 통합 셋업 가이드 (macOS / Binance USDT-M Futures)

작성자 기준: Kai Roh
대상 환경: macOS, Binance USDT-M Perpetual, 자본 $500~1000, 레버리지 3~5x, 종목 3~5개, 시간당 2~5회 거래, 수동 운용 모드

---

## Phase 0. 사전 점검 (30분)

### 0.1 Binance 계정 점검
1. Binance Futures 활성화 여부 확인 (Spot만 활성화된 계정이면 Futures 별도 활성화)
2. KYC Level 2 완료 (선물은 필수)
3. **API 키 새로 발급**:
   - Futures Trading 권한 ON
   - Spot Trading OFF (사용 안 함)
   - Withdraw OFF (절대 ON 금지)
   - IP 화이트리스트: 본인 로컬 PC 공인 IP 등록 (https://www.whatismyip.com 으로 확인)
4. **수수료 할인 활성화**:
   - BNB로 수수료 결제 ON (10% 할인)
   - 추천인/리베이트 코드 적용 가능하면 적용
5. 초기 자본을 Spot 지갑에서 USDⓈ-M Futures 지갑으로 이체

### 0.2 macOS 사전 설치
```bash
# Homebrew 없으면 설치
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Docker Desktop 설치 (가장 권장)
brew install --cask docker
# Docker Desktop 앱 실행 후 로그인

# 보조 도구
brew install jq git
```

> **소스 설치 대신 Docker 강력 권장**: TA-Lib, LightGBM, CatBoost 컴파일 이슈 회피.
> macOS에서 Apple Silicon(M1/M2/M3) 사용 중이면 더더욱 Docker.

---

## Phase 1. 프로젝트 셋업 (15분)

### 1.1 작업 디렉토리 구성
```bash
mkdir -p ~/ft_2026 && cd ~/ft_2026

# Freqtrade 공식 docker-compose 가져오기
curl -L https://raw.githubusercontent.com/freqtrade/freqtrade/stable/docker-compose.yml -o docker-compose.yml

# 이미지 pull (FreqAI 포함 이미지로 교체 예정)
# 이 가이드의 docker-compose.yml 템플릿으로 덮어쓰세요 (다음 산출물 참조)
```

이 키트의 `docker-compose.yml`을 사용하세요. **기본 이미지가 아닌 freqai 이미지를 사용**해야 ML 패키지가 포함됩니다.

### 1.2 user_data 디렉토리 초기화
```bash
docker compose run --rm freqtrade create-userdir --userdir user_data
```

### 1.3 키트 파일 복사
이 키트의 다음 파일들을 본인 환경의 해당 위치로 복사:
- `docker-compose.yml` → `~/ft_2026/`
- `user_data/config.json` → `~/ft_2026/user_data/`
- `user_data/strategies/KaiBaseStrategy.py` → `~/ft_2026/user_data/strategies/`
- `user_data/freqaimodels/LLMEnhancedModel.py` → `~/ft_2026/user_data/freqaimodels/`
- `user_data/llm/claude_client.py` → `~/ft_2026/user_data/llm/`
- `scripts/fee_reconciliation.py` → `~/ft_2026/scripts/`
- `scripts/control.sh` → `~/ft_2026/scripts/`
- `.env.example` → `~/ft_2026/.env` (값 채우기)

---

## Phase 2. 환경변수 및 설정 (20분)

### 2.1 `.env` 작성
```bash
cd ~/ft_2026
cp .env.example .env
nano .env
```
다음 값 채우기:
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`
- `ANTHROPIC_API_KEY`
- `FREQTRADE_USERNAME`, `FREQTRADE_PASSWORD` (웹 UI 로그인용)
- `JWT_SECRET` (아무 랜덤 문자열, openssl rand -hex 32 로 생성)

### 2.2 config.json 점검 포인트
키트 제공 config.json은 다음 값을 본인 환경에 맞게 조정:
- `stake_amount`: $500 자본이면 50, $1000이면 100 정도부터 시작 (자본의 10%)
- `max_open_trades`: 3 (3종목 동시 보유)
- `dry_run`: **반드시 true로 시작**
- `dry_run_wallet`: 1000
- `pair_whitelist`: 초기 후보 종목 리스트 (Phase 5 참조)

---

## Phase 3. Dry-run 검증 (1주 이상)

### 3.1 시작
```bash
# Foreground (로그 직접 확인)
docker compose up

# Background
docker compose up -d
docker compose logs -f freqtrade
```

### 3.2 웹 UI 접속
http://localhost:8080
`.env`의 `FREQTRADE_USERNAME` / `FREQTRADE_PASSWORD` 입력

### 3.3 검증 체크리스트
- [ ] 데이터 피드가 끊김 없이 들어오는가 (캔들 갱신 확인)
- [ ] 진입/청산 시그널이 발생하는가 (최소 하루 1건 이상)
- [ ] 가짜 주문이 정상 시뮬레이션되는가
- [ ] FreqAI가 학습/예측을 수행하는가 (`user_data/models/` 폴더에 모델 파일 생성 확인)
- [ ] Claude API 호출이 정상이고 비용이 예상 범위인가
- [ ] 1주 누적 수익률이 양수인가 (음수면 전략 재검토)

### 3.4 백테스트 실행
```bash
# 데이터 다운로드 (최근 90일, 5분봉)
docker compose run --rm freqtrade download-data \
  --exchange binance --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT \
  --timeframes 5m 15m 1h --days 90 --trading-mode futures

# 백테스트
docker compose run --rm freqtrade backtesting \
  --strategy KaiBaseStrategy --timeframe 5m \
  --timerange 20260101-20260501 \
  --freqaimodel LLMEnhancedModel
```

> **백테스트 fee 강제 1.5배 보수화**: config.json의 `"fee": 0.0009` (실제 0.0006의 1.5배) 적용됨.

---

## Phase 4. 소액 라이브 (2주)

### 4.1 전환 절차
1. dry-run 1주 + 백테스트 결과 모두 양수 확인
2. config.json에서 `"dry_run": false`
3. **stake_amount를 더 작게**: $500 자본이면 30, $1000이면 50 (자본의 5~6%)
4. `max_open_trades`를 2로 축소
5. 재시작:
```bash
docker compose down
docker compose up -d
```

### 4.2 일일 모니터링 루틴
매일 1회 실행:
```bash
./scripts/fee_reconciliation.py
```
- 백테스트 가정 수수료 vs 실제 청구 수수료 차이 확인
- 차이가 5% 이상이면 config.json의 `fee` 값 조정

### 4.3 끊김 대응 (수동 모드)
이동 전:
```bash
./scripts/control.sh stop_safe
# 신규 진입 차단 + 기존 포지션은 청산 시그널 대기
```
또는 즉시 전부 청산:
```bash
./scripts/control.sh emergency_close
```

복귀 후:
```bash
./scripts/control.sh start
```

> **중요**: 이동 시간이 4시간 이상이면 `emergency_close` 권장.
> 시간당 2~5회 거래 빈도라면 4시간 무방치는 평균 8~20개 시그널 손실 + 잠재적 청산 리스크.

---

## Phase 5. 본격 운용 (라이브 결과 양호 시)

### 5.1 종목 선정 기준 (3~5개)
USDT-M Perpetual 기준 우선순위:
1. **유동성**: 24h 거래대금 $5억 이상
2. **변동성**: ATR/Price 비율 1~3% (너무 낮으면 수익 기회 부족, 너무 높으면 리스크)
3. **펀딩 비율 안정성**: 절대값 0.05% 미만
4. **상관관계 분산**: BTC와 0.7 미만 상관관계인 종목 1~2개 포함

초기 후보 (2026년 5월 기준 일반 추천):
- `BTC/USDT:USDT` - 기준 자산
- `ETH/USDT:USDT` - 보조 기준
- `SOL/USDT:USDT` - 변동성 알파
- `BNB/USDT:USDT` - 펀딩 안정
- `XRP/USDT:USDT` 또는 `DOGE/USDT:USDT` - 분산용

> 실제 종목은 라이브 시작 시점에 직접 거래대금/변동성 점검 후 확정. config.json에 명시한 후보는 예시.

### 5.2 stake_amount 점진 증대
2주 라이브 결과 기준:
- 누적 수익률 +5% 이상 + 최대 낙폭 -10% 이내 → stake 1.5배
- 양수지만 미달 → stake 유지
- 음수 → stake 절반 + 전략 재점검

### 5.3 단계적 강화 로드맵
1. 기본 전략 안정화 (4주)
2. FreqAI 하이퍼파라미터 튜닝 (2주)
3. Claude API 호출을 이벤트 트리거 기반으로 최적화 (1주)
4. 다중 시간프레임 결합 (informative_pairs) (2주)
5. 동적 종목 선정(VolumePairList) 도입 (검증 후)

---

## Phase 6. 리스크 관리 하드 룰

| 항목 | 한도 | 자동화 |
|---|---|---|
| 최대 동시 포지션 | 3개 | config |
| 단일 포지션 최대 손실 | 자본의 -2% | stoploss |
| 일일 최대 손실 | 자본의 -5% | max_drawdown protection |
| 주간 최대 손실 | 자본의 -10% | 수동 정지 |
| 레버리지 | 5배 고정 | config |
| 펀딩 비율 |0.1% 초과 시 진입 차단 | 전략 코드 |

위 한도 중 하나라도 위반되면 즉시 전체 정지 후 원인 분석.

---

## Phase 7. 트러블슈팅

| 증상 | 원인 후보 | 해결 |
|---|---|---|
| Docker 컨테이너 즉시 종료 | config.json 문법 오류 | `docker compose logs freqtrade` 로그 확인 |
| API key invalid | IP 화이트리스트 누락 | Binance에서 현재 공인 IP 등록 |
| FreqAI 모델 학습 실패 | 데이터 부족 | `download-data`로 추가 다운로드 |
| Claude API rate limit | 호출 빈도 과다 | `claude_client.py`의 캐시 TTL 증가 |
| 백테스트와 라이브 수익 차이 큼 | 슬리피지/수수료 | `fee_reconciliation.py`로 보정값 갱신 |
| 펀딩 시점 손실 누적 | 펀딩 회피 로직 미작동 | 전략의 `funding_rate` 체크 활성화 |

---

## 부록: 일일 운용 체크리스트
1. 아침: `./scripts/control.sh status` 로 시스템 상태 확인
2. 점심: `./scripts/fee_reconciliation.py` 실행
3. 이동 전: `./scripts/control.sh stop_safe` 또는 `emergency_close`
4. 저녁: 웹 UI에서 일일 PnL, 수수료, 펀딩 비용 확인
5. 주말: 주간 결과 검토, 다음 주 stake 조정 결정
