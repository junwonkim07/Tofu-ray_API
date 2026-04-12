# 제가 필요한 정보

## 1) 환경 변수 값

아래 값을 `.env`에 채워주세요.

- `STORE_JWT_SECRET`: 스토어프론트 고객 JWT에서 사용하는 동일한 시크릿
- `STORE_JWT_ALGORITHM`: 보통 `HS256`
- `EVENT_WEBHOOK_SECRET`: `POST /events/order-paid` 호출 시 헤더에 넣을 시크릿 값
- `MARZBAN_URL`, `MARZBAN_USERNAME`, `MARZBAN_PASSWORD`

선택 값:

- `STORE_JWT_AUDIENCE`: JWT에서 audience 검증을 사용하는 경우
- `MARZBAN_TIMEOUT_MS`, `MARZBAN_MAX_RETRIES`

## 2) 이벤트 Payload 형식

현재 `POST /events/order-paid`는 아래 형태의 본문을 받도록 구현되어 있습니다.

```json
{
  "order": {
    "id": "order_01",
    "customer_id": "cus_01",
    "customer_email": "user@example.com"
  },
  "line_items": [
    {
      "id": "item_01",
      "title": "Pro 30 Days",
      "metadata": {
        "is_subscription": true,
        "duration_days": 30,
        "traffic_limit_gb": 100,
        "plan_id": "pro_30"
      }
    }
  ]
}
```

백엔드의 실제 이벤트 포맷이 다르면, 실제 payload 예시를 주시면 해당 형식으로 바로 매핑해드리겠습니다.

## 3) 빠른 검증 방법

1. 구독 상품에 대해 결제 완료 이벤트를 1회 발생시킵니다.
2. 고객 JWT로 `GET /store/subscriptions/me`를 호출합니다.
3. 같은 JWT로 `GET /store/orders/{id}/subscription`를 호출합니다.

실제 JWT payload 예시 1개(클레임만, 시크릿 제외)만 주시면 `customer_id`와 `sub` 매핑을 운영 환경에 맞게 더 정확하게 고정해드릴 수 있습니다.