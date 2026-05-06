# Kai Freqtrade Kit - Quick Start

[![CI](https://github.com/kai-roh/freqtrade-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/kai-roh/freqtrade-v2/actions/workflows/ci.yml)
[![Secrets Scan](https://github.com/kai-roh/freqtrade-v2/actions/workflows/secrets.yml/badge.svg)](https://github.com/kai-roh/freqtrade-v2/actions/workflows/secrets.yml)

## 5분 안에 띄우기

```bash
# 1. 키트 압축 풀기
unzip ft_kit.zip -d ~/ft_2026
cd ~/ft_2026

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 열고 실제 키 입력
nano .env

# 3. JWT secret 생성
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env

# 4. Docker 실행
docker compose pull
docker compose up -d

# 5. 로그 확인
docker compose logs -f freqtrade

# 6. 웹 UI 접속
open http://localhost:8080
```

## 디렉토리 구조

```
~/ft_2026/
├── docker-compose.yml          # Docker 설정
├── .env                        # 비밀 키 (gitignore!)
├── .env.example                # 템플릿
├── docs/
│   └── 00_GUIDE.md             # 단계별 가이드
├── scripts/
│   ├── control.sh              # 수동 제어
│   └── fee_reconciliation.py   # 수수료 대사
└── user_data/
    ├── config.json             # 메인 설정
    ├── strategies/
    │   └── KaiBaseStrategy.py  # 기본 전략
    ├── freqaimodels/
    │   └── LLMEnhancedModel.py # FreqAI 모델
    └── llm/
        └── claude_client.py    # Claude API
```

## 단계별 진행

1. **[필수]** `docs/00_GUIDE.md` 정독
2. **[필수]** Phase 0~2 완료 후 dry-run 시작
3. **[1주 후]** dry-run 결과 양호 시 백테스트
4. **[2주 후]** 소액 라이브 ($30~50 stake)
5. **[1개월 후]** 결과 양호 시 stake 점진 증대

## 자주 쓰는 명령

```bash
# 상태 확인
./scripts/control.sh status

# 이동 전 정지
./scripts/control.sh stop_safe          # 짧은 외출
./scripts/control.sh emergency_close    # 4시간+ 외출

# 복귀
./scripts/control.sh resume

# 일일 통합 리포트 (PnL + fee + Claude 비용 + 알림)
./scripts/control.sh daily_report

# 일일 수수료만 검증
docker compose run --rm freqtrade python /freqtrade/scripts/fee_reconciliation.py

# 단위 테스트 (호스트에서)
pip install -r requirements-dev.txt
./scripts/test.sh

# 백테스트 (자동화: 데이터 다운로드 + 백테스트 + 마크다운 리포트)
./scripts/run_backtest.sh --days 90
# 결과: user_data/backtest_results/<UTC-ts>/REPORT.md
# Live-Gate(Sharpe≥1.0, MDD≤15%) 판정 포함, exit code 0=PASS / 1=FAIL

# 하이퍼옵트 (자동화: SharpeHyperOptLoss 100 epochs)
./scripts/run_hyperopt.sh --epochs 200 --spaces "buy sell"
# 결과: user_data/hyperopt_results/<UTC-ts>/{REPORT.md, best_params.json, results.fthypt}
# 주의: 수십 분~수 시간 소요. 매주 1회 권장

# 백테스트 (수동)
docker compose run --rm freqtrade backtesting \
  --strategy KaiBaseStrategy \
  --freqaimodel LLMEnhancedModel \
  --timerange 20260201-20260501

# 데이터 다운로드
docker compose run --rm freqtrade download-data \
  --exchange binance --pairs BTC/USDT:USDT ETH/USDT:USDT \
  --timeframes 5m 15m 1h --days 90 --trading-mode futures
```

## 주의사항 5가지

1. **dry-run 1주 미만은 절대 라이브 금지**
2. **실제 fee가 백테스트 가정과 다르면 즉시 config 갱신** (fee_reconciliation.py)
3. **이동 시간 4시간+ 는 emergency_close 권장** (펀딩 타임 리스크)
4. **Claude API 일일 비용 모니터링** (예상 $4~9/일)
5. **API 키에 Withdraw 권한 절대 부여 금지**
