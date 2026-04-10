import hashlib
import hmac
import json
import os
import secrets
import string
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

app = FastAPI()

LEMONSQUEEZY_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET")
MARZBAN_URL = os.getenv("MARZBAN_URL", "http://localhost:8000")
MARZBAN_USERNAME = os.getenv("MARZBAN_USERNAME", "admin")
MARZBAN_PASSWORD = os.getenv("MARZBAN_PASSWORD")

PLAN_MONTHLY_VARIANT_ID = str(os.getenv("PLAN_MONTHLY_VARIANT_ID"))
PLAN_YEARLY_VARIANT_ID = str(os.getenv("PLAN_YEARLY_VARIANT_ID"))

# 400GB in bytes
DATA_LIMIT_BYTES = 400 * 1024 * 1024 * 1024


# ── Marzban 인증 토큰 받기 ──────────────────────────────────────────────────
async def get_marzban_token() -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MARZBAN_URL}/api/admin/token",
            data={"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


# ── Marzban 유저 생성 ───────────────────────────────────────────────────────
async def create_marzban_user(username: str, expire_days: int) -> dict:
    token = await get_marzban_token()
    expire_ts = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp())

    payload = {
        "username": username,
        "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
        "data_limit": DATA_LIMIT_BYTES,
        "expire": expire_ts,
        "data_limit_reset_strategy": "month",  # 매달 400GB 리셋
        "status": "active",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MARZBAN_URL}/api/user",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


# ── Marzban 유저 만료일 연장 ────────────────────────────────────────────────
async def extend_marzban_user(username: str, extra_days: int):
    token = await get_marzban_token()

    # 현재 유저 정보 조회
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MARZBAN_URL}/api/user/{username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        user = resp.json()

    current_expire = user.get("expire") or int(datetime.utcnow().timestamp())
    new_expire = current_expire + extra_days * 86400

    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{MARZBAN_URL}/api/user/{username}",
            json={"expire": new_expire, "status": "active"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


# ── 랜덤 유저명 생성 ────────────────────────────────────────────────────────
def generate_username(email: str) -> str:
    base = email.split("@")[0].replace(".", "_").replace("+", "_")[:12]
    suffix = "".join(secrets.choice(string.digits) for _ in range(6))
    return f"{base}_{suffix}"


# ── Lemon Squeezy 웹훅 서명 검증 ───────────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        LEMONSQUEEZY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── 구독 링크 조회 ──────────────────────────────────────────────────────────
async def get_subscription_url(username: str) -> str:
    token = await get_marzban_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MARZBAN_URL}/api/user/{username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        user = resp.json()
    # Marzban subscription_url 필드
    return user.get("subscription_url", "")


# ── 웹훅 엔드포인트 ─────────────────────────────────────────────────────────
@app.post("/webhook/lemonsqueezy")
async def lemonsqueezy_webhook(
    request: Request,
    x_signature: str = Header(None, alias="X-Signature"),
):
    body = await request.body()

    # 서명 검증
    if not x_signature or not verify_signature(body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(body)
    event = data.get("meta", {}).get("event_name")
    attrs = data.get("data", {}).get("attributes", {})
    variant_id = str(attrs.get("variant_id", ""))
    customer_email = attrs.get("user_email") or attrs.get("customer_email", "")
    order_id = str(data.get("data", {}).get("id", ""))

    # 플랜별 만료일 계산
    if variant_id == PLAN_MONTHLY_VARIANT_ID:
        expire_days = 31
    elif variant_id == PLAN_YEARLY_VARIANT_ID:
        expire_days = 366
    else:
        # 알 수 없는 플랜은 무시
        return JSONResponse({"status": "ignored", "reason": "unknown variant"})

    # ── 신규 구독 ──────────────────────────────────────────────────────────
    if event == "subscription_created":
        username = generate_username(customer_email)
        user = await create_marzban_user(username, expire_days)
        sub_url = await get_subscription_url(username)

        return JSONResponse({
            "status": "ok",
            "event": event,
            "username": username,
            "subscription_url": sub_url,
        })

    # ── 구독 갱신 ──────────────────────────────────────────────────────────
    elif event == "subscription_updated":
        # custom_data에 저장된 username으로 연장
        username = data.get("meta", {}).get("custom_data", {}).get("marzban_username")
        if username:
            await extend_marzban_user(username, expire_days)

        return JSONResponse({"status": "ok", "event": event})

    # ── 구독 취소/만료 ─────────────────────────────────────────────────────
    elif event in ("subscription_expired", "subscription_cancelled"):
        username = data.get("meta", {}).get("custom_data", {}).get("marzban_username")
        if username:
            token = await get_marzban_token()
            async with httpx.AsyncClient() as client:
                await client.put(
                    f"{MARZBAN_URL}/api/user/{username}",
                    json={"status": "disabled"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        return JSONResponse({"status": "ok", "event": event})

    return JSONResponse({"status": "ignored", "event": event})


# ── 결제 완료 페이지용: 구독 링크 조회 API ──────────────────────────────────
@app.get("/subscription/{username}")
async def get_user_subscription(username: str):
    """
    Lemon Squeezy 결제 완료 페이지에서 호출.
    ?username=xxx 로 구독 링크 반환.
    """
    try:
        sub_url = await get_subscription_url(username)
        return JSONResponse({"subscription_url": sub_url, "username": username})
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")


@app.get("/health")
async def health():
    return {"status": "ok"}
