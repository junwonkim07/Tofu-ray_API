# App/Worker 분리 실행 노트

목표 구조:
- App 1개 (`main.py`)
- Worker 1개 (`worker.py`)
- Postgres 1개 (관리형 권장)
- Redis 1개 (관리형 권장)

## 1) 최초 1회 준비

```powershell
cd C:\Users\junwo\OneDrive\문서\github\Tofu-ray_Backend
Copy-Item .env.example .env
```

`.env` 필수값:

```env
STORE_JWT_SECRET=change-me
STORE_JWT_ALGORITHM=HS256
EVENT_WEBHOOK_SECRET=change-me

# 관리형 Postgres (Supabase 등)
DATABASE_URL=postgresql://USER:PASSWORD@HOST:6543/postgres?sslmode=require

# 관리형 Redis (Upstash/Redis Cloud 등)
REDIS_URL=redis://default:PASSWORD@HOST:PORT

ORDER_PAID_QUEUE_NAME=order_paid_events

MARZBAN_URL=http://localhost:8000
MARZBAN_USERNAME=admin
MARZBAN_PASSWORD=change-me
```

## 2) 관리형 DB/Redis 준비 (Docker 없이)

### 2-1. Postgres (Supabase 예시)

- Supabase 프로젝트 생성
- Connection string 복사
- `sslmode=require` 포함 여부 확인

예시:

```env
DATABASE_URL=postgresql://postgres.xxxxx:password@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require
```

### 2-2. Redis (Upstash 예시)

- Upstash Redis 생성
- `REDIS_URL` 복사

예시:

```env
REDIS_URL=redis://default:password@your-endpoint.upstash.io:6379
```

## 3) Python 의존성 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 4) App 실행 (터미널 A)

```powershell
cd C:\Users\junwo\OneDrive\문서\github\Tofu-ray_Backend
.\.venv\Scripts\Activate.ps1
uvicorn main:app --host 0.0.0.0 --port 9000 --reload
```

## 5) Worker 실행 (터미널 B)

```powershell
cd C:\Users\junwo\OneDrive\문서\github\Tofu-ray_Backend
.\.venv\Scripts\Activate.ps1
python worker.py
```

## 6) 빠른 검증

헬스체크:

```powershell
Invoke-RestMethod http://localhost:9000/health
```

이벤트 큐 적재 테스트:

```powershell
$headers = @{
  "Content-Type" = "application/json"
  "X-Event-Secret" = "change-me"
}

$body = @'
{
  "order": {
    "id": "order_test_01",
    "customer_id": "cus_test_01",
    "customer_email": "test@example.com"
  },
  "line_items": [
    {
      "id": "item_test_01",
      "title": "Pro 30 Days",
      "metadata": {
        "is_subscription": true,
        "duration_days": 30,
        "traffic_limit_gb": 100
      }
    }
  ]
}
'@

Invoke-RestMethod -Method Post -Uri http://localhost:9000/events/order-paid -Headers $headers -Body $body
```

정상 시 기대값:
- App 응답: `status=accepted`, `queued=true`
- Worker 로그: `Processed queued event ...`

## 7) 실패 시 우선 점검

1. Postgres 접속 실패
- `DATABASE_URL` 오타/비밀번호 확인
- `sslmode=require` 확인

2. Redis 접속 실패
- `REDIS_URL` 오타 확인
- Redis provider의 TLS 요구사항 확인

3. Worker가 이벤트를 못 받음
- App 응답에 `queued=true` 인지 확인
- `ORDER_PAID_QUEUE_NAME`가 app/worker 동일한지 확인

## 8) (선택) 로컬 Docker 인프라로 대체 실행

Docker가 있을 때만 사용:

```powershell
docker compose -f docker-compose.infra.yml up -d
docker compose -f docker-compose.infra.yml ps
```