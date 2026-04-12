import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

try:
    import redis.asyncio as redis_asyncio
except ImportError:
    redis_asyncio = None

load_dotenv()

logger = logging.getLogger("subscription-service")
logging.basicConfig(level=logging.INFO)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_or_none(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = os.getenv("SUBSCRIPTIONS_DB_PATH", "subscriptions.db")
DB_BACKEND = "postgres" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"

REDIS_URL = os.getenv("REDIS_URL", "").strip()
ORDER_PAID_QUEUE_NAME = os.getenv("ORDER_PAID_QUEUE_NAME", "order_paid_events")

STORE_JWT_SECRET = os.getenv("STORE_JWT_SECRET", "")
STORE_JWT_ALGORITHM = os.getenv("STORE_JWT_ALGORITHM", "HS256")
STORE_JWT_AUDIENCE = os.getenv("STORE_JWT_AUDIENCE", "")

EVENT_WEBHOOK_SECRET = os.getenv("EVENT_WEBHOOK_SECRET", "")

LEMONSQUEEZY_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")
PLAN_MONTHLY_VARIANT_ID = str(os.getenv("PLAN_MONTHLY_VARIANT_ID", ""))
PLAN_YEARLY_VARIANT_ID = str(os.getenv("PLAN_YEARLY_VARIANT_ID", ""))

MARZBAN_URL = os.getenv("MARZBAN_URL", "http://localhost:8000")
MARZBAN_USERNAME = os.getenv("MARZBAN_USERNAME", "admin")
MARZBAN_PASSWORD = os.getenv("MARZBAN_PASSWORD", "")
MARZBAN_TIMEOUT_MS = int(os.getenv("MARZBAN_TIMEOUT_MS", "10000"))
MARZBAN_MAX_RETRIES = int(os.getenv("MARZBAN_MAX_RETRIES", "3"))

# Defaults to 400GB. Can be overridden per product metadata with traffic_limit_gb.
DATA_LIMIT_BYTES = int(os.getenv("MARZBAN_DEFAULT_DATA_LIMIT_BYTES", str(400 * 1024 * 1024 * 1024)))

security = HTTPBearer(auto_error=False)


class OrderSummary(BaseModel):
    id: str
    customer_id: str
    customer_email: Optional[str] = None


class OrderLineItem(BaseModel):
    id: str
    title: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderPaidEvent(BaseModel):
    order: OrderSummary
    line_items: list[OrderLineItem] = Field(default_factory=list)


def get_conn() -> sqlite3.Connection:
    if DB_BACKEND == "postgres":
        if psycopg is None or dict_row is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is set")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_query(query: str) -> str:
    if DB_BACKEND == "postgres":
        return query.replace("?", "%s")
    return query


def init_db() -> None:
    conn = get_conn()
    try:
        if DB_BACKEND == "postgres":
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        line_item_id TEXT NOT NULL,
                        customer_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        marzban_username TEXT,
                        subscription_url TEXT,
                        expires_at TEXT,
                        product_title TEXT,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriptions_order_line_item
                    ON subscriptions(order_id, line_item_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS ix_subscriptions_customer_id
                    ON subscriptions(customer_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS ix_subscriptions_status
                    ON subscriptions(status)
                    """
                )
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    line_item_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    marzban_username TEXT,
                    subscription_url TEXT,
                    expires_at TEXT,
                    product_title TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriptions_order_line_item
                    ON subscriptions(order_id, line_item_id);

                CREATE INDEX IF NOT EXISTS ix_subscriptions_customer_id
                    ON subscriptions(customer_id);

                CREATE INDEX IF NOT EXISTS ix_subscriptions_status
                    ON subscriptions(status);
                """
            )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


def row_to_subscription(row: sqlite3.Row) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    raw_metadata = row["metadata"]
    if raw_metadata:
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            metadata = {}

    return {
        "id": row["id"],
        "status": row["status"],
        "order_id": row["order_id"],
        "line_item_id": row["line_item_id"],
        "marzban_username": row["marzban_username"],
        "subscription_url": row["subscription_url"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "product_title": row["product_title"],
        "metadata": metadata,
    }


def mask_subscription_url(url: Optional[str]) -> str:
    if not url:
        return ""
    if len(url) <= 12:
        return "***"
    return f"{url[:8]}***{url[-4:]}"


def normalize_username(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value)
    normalized = normalized.strip("_")
    return normalized[:24] if normalized else "user"


def build_marzban_username(customer_id: str, order_id: str, line_item_id: str) -> str:
    suffix = hashlib.sha1(f"{order_id}:{line_item_id}".encode()).hexdigest()[:8]
    base = normalize_username(customer_id)[:15]
    return f"{base}_{suffix}"


def parse_duration_days(metadata: dict[str, Any]) -> Optional[int]:
    duration = metadata.get("duration_days")
    if duration is None:
        return None
    try:
        value = int(duration)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_traffic_limit_bytes(metadata: dict[str, Any]) -> int:
    traffic_limit_gb = metadata.get("traffic_limit_gb")
    if traffic_limit_gb is None:
        return DATA_LIMIT_BYTES
    try:
        gb = int(traffic_limit_gb)
    except (TypeError, ValueError):
        return DATA_LIMIT_BYTES
    if gb <= 0:
        return DATA_LIMIT_BYTES
    return gb * 1024 * 1024 * 1024


def compute_expires_at(metadata: dict[str, Any]) -> Optional[datetime]:
    expires_at_raw = metadata.get("expires_at")
    if expires_at_raw:
        parsed = parse_iso_or_none(str(expires_at_raw))
        if parsed:
            return parsed.astimezone(timezone.utc)

    duration_days = parse_duration_days(metadata)
    if duration_days:
        return datetime.now(timezone.utc) + timedelta(days=duration_days)

    return None


async def db_fetch_all(query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    def _run() -> list[sqlite3.Row]:
        conn = get_conn()
        try:
            if DB_BACKEND == "postgres":
                with conn.cursor() as cur:
                    cur.execute(db_query(query), params)
                    return cur.fetchall()

            cur = conn.execute(db_query(query), params)
            return cur.fetchall()
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def db_fetch_one(query: str, params: tuple[Any, ...]) -> Optional[sqlite3.Row]:
    def _run() -> Optional[sqlite3.Row]:
        conn = get_conn()
        try:
            if DB_BACKEND == "postgres":
                with conn.cursor() as cur:
                    cur.execute(db_query(query), params)
                    return cur.fetchone()

            cur = conn.execute(db_query(query), params)
            return cur.fetchone()
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def db_insert_subscription(payload: dict[str, Any]) -> bool:
    def _run() -> bool:
        conn = get_conn()
        try:
            if DB_BACKEND == "postgres":
                with conn.cursor() as cur:
                    cur.execute(
                        db_query(
                            """
                            INSERT INTO subscriptions (
                                id,
                                order_id,
                                line_item_id,
                                customer_id,
                                status,
                                marzban_username,
                                subscription_url,
                                expires_at,
                                product_title,
                                metadata,
                                created_at,
                                updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(order_id, line_item_id) DO NOTHING
                            """
                        ),
                        (
                            payload["id"],
                            payload["order_id"],
                            payload["line_item_id"],
                            payload["customer_id"],
                            payload["status"],
                            payload.get("marzban_username"),
                            payload.get("subscription_url"),
                            payload.get("expires_at"),
                            payload.get("product_title"),
                            json.dumps(payload.get("metadata", {}), ensure_ascii=True),
                            payload["created_at"],
                            payload["updated_at"],
                        ),
                    )
                    rowcount = cur.rowcount
            else:
                cur = conn.execute(
                    db_query(
                        """
                        INSERT INTO subscriptions (
                            id,
                            order_id,
                            line_item_id,
                            customer_id,
                            status,
                            marzban_username,
                            subscription_url,
                            expires_at,
                            product_title,
                            metadata,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(order_id, line_item_id) DO NOTHING
                        """
                    ),
                    (
                        payload["id"],
                        payload["order_id"],
                        payload["line_item_id"],
                        payload["customer_id"],
                        payload["status"],
                        payload.get("marzban_username"),
                        payload.get("subscription_url"),
                        payload.get("expires_at"),
                        payload.get("product_title"),
                        json.dumps(payload.get("metadata", {}), ensure_ascii=True),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                rowcount = cur.rowcount
            conn.commit()
            return rowcount > 0
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


def verify_event_secret(x_event_secret: Optional[str]) -> None:
    if not EVENT_WEBHOOK_SECRET:
        return
    if not x_event_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing event secret")
    if not hmac.compare_digest(EVENT_WEBHOOK_SECRET, x_event_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid event secret")


def model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[attr-defined]
    return model.dict()  # type: ignore[no-any-return]


def parse_order_paid_event(data: dict[str, Any]) -> OrderPaidEvent:
    if hasattr(OrderPaidEvent, "model_validate"):
        return OrderPaidEvent.model_validate(data)  # type: ignore[attr-defined]
    return OrderPaidEvent.parse_obj(data)


async def close_redis(client) -> None:
    closer = getattr(client, "aclose", None)
    if callable(closer):
        await closer()
        return

    closer = getattr(client, "close", None)
    if callable(closer):
        result = closer()
        if asyncio.iscoroutine(result):
            await result


async def enqueue_order_paid_event(payload: OrderPaidEvent) -> None:
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is not configured")
    if redis_asyncio is None:
        raise RuntimeError("redis package is required when REDIS_URL is set")

    client = redis_asyncio.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        await client.rpush(ORDER_PAID_QUEUE_NAME, json.dumps(model_dump(payload), ensure_ascii=True))
    finally:
        await close_redis(client)


async def run_order_paid_worker() -> None:
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is required for worker mode")
    if redis_asyncio is None:
        raise RuntimeError("redis package is required for worker mode")

    logger.info("Starting worker. queue=%s db_backend=%s", ORDER_PAID_QUEUE_NAME, DB_BACKEND)

    client = redis_asyncio.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        while True:
            item = await client.blpop(ORDER_PAID_QUEUE_NAME, timeout=5)
            if not item:
                continue

            _, raw_payload = item
            try:
                payload = parse_order_paid_event(json.loads(raw_payload))
                result = await process_order_paid_event(payload)
                logger.info(
                    "Processed queued event order_id=%s created=%s pending=%s skipped=%s",
                    result.get("order_id"),
                    result.get("created_count"),
                    result.get("pending_count"),
                    result.get("skipped_count"),
                )
            except Exception:
                logger.exception("Failed processing queued order-paid event")
    finally:
        await close_redis(client)


def extract_customer_id_from_token(token: str) -> str:
    if not STORE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STORE_JWT_SECRET is not configured",
        )

    decode_kwargs: dict[str, Any] = {
        "algorithms": [STORE_JWT_ALGORITHM],
    }
    if STORE_JWT_AUDIENCE:
        decode_kwargs["audience"] = STORE_JWT_AUDIENCE
    else:
        decode_kwargs["options"] = {"verify_aud": False}

    try:
        payload = jwt.decode(token, STORE_JWT_SECRET, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    customer_id = payload.get("customer_id") or payload.get("sub")
    if not customer_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="customer_id is missing in token")
    return str(customer_id)


def get_current_customer_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return extract_customer_id_from_token(credentials.credentials)


async def marzban_request_with_retry(request_fn):
    last_error: Optional[Exception] = None
    delay_seconds = 0.5

    for attempt in range(1, MARZBAN_MAX_RETRIES + 1):
        try:
            return await request_fn()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if 400 <= status_code < 500 and status_code != 429:
                raise
            last_error = exc
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc

        if attempt < MARZBAN_MAX_RETRIES:
            await asyncio.sleep(delay_seconds)
            delay_seconds *= 2

    raise RuntimeError("Marzban API call failed after retries") from last_error


def get_http_timeout() -> httpx.Timeout:
    seconds = max(1, MARZBAN_TIMEOUT_MS) / 1000
    return httpx.Timeout(seconds)


async def get_marzban_token(client: httpx.AsyncClient) -> str:
    async def _request():
        resp = await client.post(
            f"{MARZBAN_URL}/api/admin/token",
            data={"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD},
        )
        resp.raise_for_status()
        return resp

    response = await marzban_request_with_retry(_request)
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Marzban token response does not include access_token")
    return token


async def get_marzban_user(client: httpx.AsyncClient, token: str, username: str) -> Optional[dict[str, Any]]:
    async def _request():
        resp = await client.get(
            f"{MARZBAN_URL}/api/user/{username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            return resp
        resp.raise_for_status()
        return resp

    response = await marzban_request_with_retry(_request)
    if response.status_code == 404:
        return None
    return response.json()


async def create_marzban_user(
    client: httpx.AsyncClient,
    token: str,
    username: str,
    expires_at: Optional[datetime],
    data_limit_bytes: int,
) -> dict[str, Any]:
    expire_ts = int(expires_at.timestamp()) if expires_at else None

    payload: dict[str, Any] = {
        "username": username,
        "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
        "data_limit": data_limit_bytes,
        "data_limit_reset_strategy": "month",
        "status": "active",
    }
    if expire_ts:
        payload["expire"] = expire_ts

    async def _request():
        resp = await client.post(
            f"{MARZBAN_URL}/api/user",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp

    response = await marzban_request_with_retry(_request)
    return response.json()


async def update_marzban_user(
    client: httpx.AsyncClient,
    token: str,
    username: str,
    expires_at: Optional[datetime],
) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "active"}
    if expires_at:
        body["expire"] = int(expires_at.timestamp())

    async def _request():
        resp = await client.put(
            f"{MARZBAN_URL}/api/user/{username}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp

    response = await marzban_request_with_retry(_request)
    return response.json()


async def disable_marzban_user(client: httpx.AsyncClient, token: str, username: str) -> None:
    async def _request():
        resp = await client.put(
            f"{MARZBAN_URL}/api/user/{username}",
            json={"status": "disabled"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp

    await marzban_request_with_retry(_request)


def is_subscription_item(line_item: OrderLineItem) -> bool:
    raw = line_item.metadata.get("is_subscription", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() in {"1", "true", "yes", "y"}
    return False


async def issue_subscription(order: OrderSummary, line_item: OrderLineItem) -> dict[str, Any]:
    metadata = line_item.metadata
    expires_at = compute_expires_at(metadata)
    if expires_at is None:
        raise ValueError("Subscription product metadata requires duration_days or expires_at")

    username = build_marzban_username(order.customer_id, order.id, line_item.id)
    data_limit_bytes = parse_traffic_limit_bytes(metadata)

    async with httpx.AsyncClient(timeout=get_http_timeout()) as client:
        token = await get_marzban_token(client)
        user = await get_marzban_user(client, token, username)
        if user is None:
            user = await create_marzban_user(client, token, username, expires_at, data_limit_bytes)
        else:
            user = await update_marzban_user(client, token, username, expires_at)

    return {
        "username": username,
        "subscription_url": user.get("subscription_url"),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
    }


async def ensure_subscription_row(order: OrderSummary, line_item: OrderLineItem) -> dict[str, Any]:
    existing = await db_fetch_one(
        """
        SELECT *
        FROM subscriptions
        WHERE order_id = ? AND line_item_id = ?
        """,
        (order.id, line_item.id),
    )
    if existing:
        return {"created": False, "status": "exists", "subscription": row_to_subscription(existing)}

    now = utcnow_iso()
    metadata = line_item.metadata or {}
    sub_id = f"sub_{hashlib.sha1(f'{order.id}:{line_item.id}'.encode()).hexdigest()[:16]}"
    marzban_username = build_marzban_username(order.customer_id, order.id, line_item.id)

    try:
        issued = await issue_subscription(order, line_item)
        record = {
            "id": sub_id,
            "order_id": order.id,
            "line_item_id": line_item.id,
            "customer_id": order.customer_id,
            "status": "active",
            "marzban_username": issued["username"],
            "subscription_url": issued.get("subscription_url"),
            "expires_at": issued.get("expires_at"),
            "product_title": line_item.title,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }
    except Exception as exc:
        logger.exception("Subscription issue failed for order=%s line_item=%s", order.id, line_item.id)
        record = {
            "id": sub_id,
            "order_id": order.id,
            "line_item_id": line_item.id,
            "customer_id": order.customer_id,
            "status": "pending",
            "marzban_username": marzban_username,
            "subscription_url": None,
            "expires_at": None,
            "product_title": line_item.title,
            "metadata": {
                **metadata,
                "issue_error": str(exc),
            },
            "created_at": now,
            "updated_at": now,
        }

    created = await db_insert_subscription(record)
    row = await db_fetch_one(
        """
        SELECT *
        FROM subscriptions
        WHERE order_id = ? AND line_item_id = ?
        """,
        (order.id, line_item.id),
    )

    if not row:
        raise HTTPException(status_code=500, detail="Failed to persist subscription")

    return {
        "created": created,
        "status": "created" if created else "exists",
        "subscription": row_to_subscription(row),
    }


@app.get("/store/subscriptions/me")
async def list_my_subscriptions(customer_id: str = Depends(get_current_customer_id)):
    rows = await db_fetch_all(
        """
        SELECT *
        FROM subscriptions
        WHERE customer_id = ?
        ORDER BY created_at DESC
        """,
        (customer_id,),
    )
    return {"subscriptions": [row_to_subscription(row) for row in rows]}


@app.get("/store/orders/{order_id}/subscription")
async def retrieve_order_subscription(order_id: str, customer_id: str = Depends(get_current_customer_id)):
    row = await db_fetch_one(
        """
        SELECT *
        FROM subscriptions
        WHERE order_id = ? AND customer_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (order_id, customer_id),
    )

    if row is None:
        return {"subscription": None}

    return {"subscription": row_to_subscription(row)}


async def process_order_paid_event(payload: OrderPaidEvent) -> dict[str, Any]:
    created_count = 0
    pending_count = 0
    skipped_count = 0
    results: list[dict[str, Any]] = []

    for line_item in payload.line_items:
        if not is_subscription_item(line_item):
            skipped_count += 1
            results.append({"line_item_id": line_item.id, "status": "skipped"})
            continue

        result = await ensure_subscription_row(payload.order, line_item)
        subscription = result["subscription"]
        if result["status"] == "created":
            if subscription["status"] == "pending":
                pending_count += 1
            else:
                created_count += 1

        results.append(
            {
                "line_item_id": line_item.id,
                "result": result["status"],
                "subscription_id": subscription["id"],
                "status": subscription["status"],
                "subscription_url": mask_subscription_url(subscription.get("subscription_url")),
            }
        )

    return {
        "status": "ok",
        "order_id": payload.order.id,
        "created_count": created_count,
        "pending_count": pending_count,
        "skipped_count": skipped_count,
        "results": results,
    }


@app.post("/events/order-paid")
async def handle_order_paid_event(
    payload: OrderPaidEvent,
    x_event_secret: Optional[str] = Header(None, alias="X-Event-Secret"),
):
    verify_event_secret(x_event_secret)

    if REDIS_URL:
        await enqueue_order_paid_event(payload)
        return {
            "status": "accepted",
            "queued": True,
            "order_id": payload.order.id,
            "queue": ORDER_PAID_QUEUE_NAME,
        }

    return await process_order_paid_event(payload)


def verify_signature(body: bytes, signature: str) -> bool:
    if not LEMONSQUEEZY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(LEMONSQUEEZY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook/lemonsqueezy")
async def lemonsqueezy_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
):
    body = await request.body()
    if not x_signature or not verify_signature(body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(body)
    event = data.get("meta", {}).get("event_name")
    attrs = data.get("data", {}).get("attributes", {})
    variant_id = str(attrs.get("variant_id", ""))
    customer_email = attrs.get("user_email") or attrs.get("customer_email") or "anonymous@example.com"
    order_id = str(data.get("data", {}).get("id", ""))

    if event == "subscription_created":
        if variant_id == PLAN_MONTHLY_VARIANT_ID:
            duration_days = 31
        elif variant_id == PLAN_YEARLY_VARIANT_ID:
            duration_days = 366
        else:
            return {"status": "ignored", "reason": "unknown variant"}

        payload = OrderPaidEvent(
            order=OrderSummary(id=order_id, customer_id=normalize_username(customer_email), customer_email=customer_email),
            line_items=[
                OrderLineItem(
                    id=f"lemonsqueezy_{order_id}",
                    title=attrs.get("product_name") or "Lemon Squeezy Subscription",
                    metadata={
                        "is_subscription": True,
                        "duration_days": duration_days,
                        "variant_id": variant_id,
                        "source": "lemonsqueezy",
                    },
                )
            ],
        )
        return await process_order_paid_event(payload)

    if event in {"subscription_expired", "subscription_cancelled"}:
        username = data.get("meta", {}).get("custom_data", {}).get("marzban_username")
        if username:
            async with httpx.AsyncClient(timeout=get_http_timeout()) as client:
                token = await get_marzban_token(client)
                await disable_marzban_user(client, token, username)
        return {"status": "ok", "event": event}

    return {"status": "ignored", "event": event}


@app.get("/subscription/{username}")
async def get_user_subscription(username: str):
    async with httpx.AsyncClient(timeout=get_http_timeout()) as client:
        token = await get_marzban_token(client)
        user = await get_marzban_user(client, token, username)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

    return {
        "subscription_url": user.get("subscription_url"),
        "username": username,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}