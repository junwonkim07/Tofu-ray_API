# Marzban 구독 서비스 핸드오프 문서 (다른 Agent용)

이 문서는 현재 저장소 상태, 이미 반영된 프론트 변경점, 그리고 다음 에이전트가 바로 실행해야 할 백엔드 작업을 상세히 정리한 실행 가이드입니다.

참고 계약 문서:
- [marzban-medusa-integration.md](./marzban-medusa-integration.md)

## 0) 현재 상황 요약

- 이 저장소는 Medusa Storefront(Next.js) 저장소다.
- Marzban 발급 핵심 로직은 이 저장소가 아니라 Medusa 백엔드 저장소에서 구현해야 한다.
- 프론트는 이미 구독 데이터를 표시할 준비가 끝나 있다.
- 현재 브랜치 상태: main, origin/main 대비 ahead 1 (작업 커밋 존재)

핵심 결론:
- 다음 작업자는 백엔드 API 2개와 이벤트 핸들러를 구현하면 프론트 화면이 바로 동작한다.

## 1) 이미 반영된 프론트 코드 맵

아래 파일은 이미 구현 완료 상태다. 백엔드 작업 전후 확인에 사용한다.

### 1-1. 타입/데이터 레이어

- [src/types/subscription.ts](../src/types/subscription.ts)
  - StoreSubscription 타입
  - 목록 응답 타입, 주문별 응답 타입

- [src/lib/data/subscriptions.ts](../src/lib/data/subscriptions.ts)
  - listMySubscriptions(): GET /store/subscriptions/me 호출
  - retrieveOrderSubscription(orderId): GET /store/orders/:id/subscription 호출
  - 404 응답은 빈 목록 또는 null 처리

### 1-2. UI 컴포넌트

- [src/modules/subscriptions/components/subscription-card/index.tsx](../src/modules/subscriptions/components/subscription-card/index.tsx)
  - 상태 badge(active/pending/expired/cancelled)
  - 만료일/발급일/username 표시
  - 링크 열기 + 클립보드 복사 버튼
  - 링크 미발급 시 "발급 중" 문구 표시

- [src/modules/account/components/subscription-overview/index.tsx](../src/modules/account/components/subscription-overview/index.tsx)
  - 구독 목록 렌더
  - 빈 상태 UI 렌더

### 1-3. 라우팅/페이지 연결

- [src/app/[countryCode]/(main)/account/@dashboard/subscriptions/page.tsx](../src/app/%5BcountryCode%5D/(main)/account/@dashboard/subscriptions/page.tsx)
  - 계정 Subscriptions 페이지

- [src/modules/account/components/account-nav/index.tsx](../src/modules/account/components/account-nav/index.tsx)
  - 계정 네비게이션에 Subscriptions 메뉴 추가

- [src/app/[countryCode]/(main)/order/[id]/confirmed/page.tsx](../src/app/%5BcountryCode%5D/(main)/order/%5Bid%5D/confirmed/page.tsx)
  - 주문 완료 페이지에서 subscription 조회 후 템플릿에 전달

- [src/modules/order/templates/order-completed-template.tsx](../src/modules/order/templates/order-completed-template.tsx)
  - 구독 카드 렌더링 영역 추가

- [src/app/[countryCode]/(main)/account/@dashboard/orders/details/[id]/page.tsx](../src/app/%5BcountryCode%5D/(main)/account/@dashboard/orders/details/%5Bid%5D/page.tsx)
  - 주문 상세 페이지에서 subscription 조회 후 템플릿에 전달

- [src/modules/order/templates/order-details-template.tsx](../src/modules/order/templates/order-details-template.tsx)
  - 주문 상세 내 구독 카드 렌더링 추가

## 2) 백엔드 저장소에서 해야 할 일 (핵심)

다음 에이전트는 Medusa 백엔드 저장소로 이동해서 아래 순서대로 구현한다.

### 2-1. 데이터 모델(테이블) 추가

테이블명 예시: subscriptions

필수 컬럼:
- id
- order_id
- line_item_id
- customer_id
- status
- marzban_username
- subscription_url
- expires_at
- product_title
- metadata (jsonb)
- created_at
- updated_at

권장 인덱스/제약:
- unique(order_id, line_item_id)  // 중복 발급 방지
- index(customer_id)
- index(status)

### 2-2. Store API 2개 구현

1) GET /store/subscriptions/me
- 인증: 고객 JWT 필수
- 동작: customer_id 기준 구독 목록 반환
- 정렬: created_at desc

2) GET /store/orders/:id/subscription
- 인증: 고객 JWT 필수
- 동작: 주문 소유권 확인 후 해당 주문 구독 1건(또는 null) 반환
- 보안: 타 고객 주문 접근 시 403 또는 404

### 2-3. 결제 완료 이벤트 핸들러 구현

트리거 조건:
- 결제가 확정된 주문 이벤트

핸들러 단계:
1. 이벤트 payload에서 orderId 조회
2. 주문 조회 및 line items 로드
3. 구독 상품인지 판별 (metadata 기반)
4. 기존 subscription 존재 여부 확인
5. 없으면 Marzban API 호출하여 발급
6. DB 저장 (status=active 또는 pending)
7. 실패 시 로그 + 재시도 큐

주의:
- 이벤트 중복 수신을 가정하고 idempotent 하게 작성
- Marzban API timeout/retry/backoff 필요

## 3) API 계약 예시 (백엔드가 맞춰야 함)

### 3-1. GET /store/subscriptions/me 응답 예시

{
  "subscriptions": [
    {
      "id": "sub_01",
      "status": "active",
      "order_id": "order_01",
      "line_item_id": "item_01",
      "marzban_username": "user_001",
      "subscription_url": "https://sub.example.com/abc",
      "expires_at": "2026-05-01T00:00:00.000Z",
      "created_at": "2026-04-12T01:00:00.000Z",
      "product_title": "Pro 30 Days",
      "metadata": {
        "duration_days": 30,
        "traffic_limit_gb": 100
      }
    }
  ]
}

### 3-2. GET /store/orders/:id/subscription 응답 예시

{
  "subscription": {
    "id": "sub_01",
    "status": "active",
    "order_id": "order_01",
    "line_item_id": "item_01",
    "marzban_username": "user_001",
    "subscription_url": "https://sub.example.com/abc",
    "expires_at": "2026-05-01T00:00:00.000Z",
    "created_at": "2026-04-12T01:00:00.000Z",
    "product_title": "Pro 30 Days",
    "metadata": {}
  }
}

구독 미발급 상태 응답 예시:

{
  "subscription": null
}

## 4) 상품 메타데이터 규칙 (구독 판별)

Variant metadata 예시:

{
  "is_subscription": true,
  "plan_id": "pro_30",
  "duration_days": 30,
  "traffic_limit_gb": 100,
  "device_limit": 3
}

백엔드 판별 규칙 제안:
- is_subscription = true 일 때만 발급 처리
- duration_days 또는 expires_at 계산 가능한 필드 필수

## 5) Marzban 발급 서비스 예시 로직

아래는 구현 의도를 전달하기 위한 의사코드다.

1. createOrGetMarzbanUser(customer)
2. issueSubscription(user, planMetadata)
3. return { username, subscriptionUrl, expiresAt }

실제 코드에서 필요한 보호장치:
- timeout
- retry(예: 3회)
- 4xx/5xx 분기 처리
- 민감 로그 마스킹

## 6) 다른 Agent가 바로 시작할 때 실행 절차

### Step A. 백엔드 저장소로 이동

- Medusa 백엔드 repo open
- env에 아래 값 추가
  - MARZBAN_BASE_URL
  - MARZBAN_API_KEY
  - MARZBAN_TIMEOUT_MS (optional)

### Step B. 모델/마이그레이션부터 작성

- subscriptions 테이블 생성
- unique(order_id, line_item_id) 제약 확인

### Step C. store API 2개 우선 구현

- /store/subscriptions/me
- /store/orders/:id/subscription

프론트에서 즉시 확인 가능한 최소 목표:
- 계정 Subscriptions 페이지에서 mock이 아닌 실제 데이터가 보일 것

### Step D. 이벤트 핸들러 연결

- 결제 완료 이벤트 수신 시 발급
- 발급 결과 DB 저장

### Step E. 통합 테스트

- 결제 1건 생성
- subscription row 생성 확인
- storefront에서 링크 노출 확인

## 7) 테스트 체크리스트 (완료 기준)

- [ ] 로그인 고객 A가 본인 구독 목록을 볼 수 있다.
- [ ] 고객 A가 고객 B 주문 id로 조회 시 접근 거부된다.
- [ ] 주문 결제 완료 후 subscription row가 생성된다.
- [ ] 중복 이벤트 수신 시 row가 1개만 유지된다.
- [ ] subscription_url이 있을 때 화면에서 링크 열기/복사가 된다.
- [ ] subscription_url이 없을 때 화면에서 발급 대기 문구가 보인다.

## 8) 장애/운영 가이드

- [ ] 발급 실패 시 pending 상태 유지 + 재시도 큐 등록
- [ ] 재시도 횟수 초과 시 운영자 알림
- [ ] 환불/취소 이벤트 수신 시 구독 비활성화 정책 적용
- [ ] 로그에는 subscription_url 전체를 저장하지 않는다.

## 9) 빠른 시작 (오늘 바로 진행용)

1. 백엔드에서 subscriptions 모델 + 마이그레이션 생성
2. /store/subscriptions/me, /store/orders/:id/subscription 구현
3. 결제 완료 이벤트 핸들러에서 Marzban 발급 연결
4. storefront 계정/주문 화면에서 실제 링크 노출 검증

---

## 메모

- 프론트 저장소에서 더 이상 막히는 지점은 거의 없다.
- 실제 진척은 백엔드 이벤트 핸들러와 API 구현 속도에 달려 있다.
- 구현 중 API 응답 형태를 바꿀 경우, 반드시 이 문서와 marzban-medusa-integration.md를 함께 업데이트한다.
