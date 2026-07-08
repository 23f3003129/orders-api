from fastapi import FastAPI, Header, HTTPException, Query, Depends, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uuid
import time

# ----------------------------
# Basic config and data
# ----------------------------

app = FastAPI()

# CORS: allow all origins so the grader page can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Total orders T
T = 52

# Fixed catalog of orders with ids 1..T
orders_catalog = [
    {"id": i, "name": f"Order {i}"}
    for i in range(1, T + 1)
]

# Store created orders and idempotency mapping
created_orders = {}          # order_id -> order dict
idempotency_store = {}       # idempotency_key -> order_id

# Rate limiting config
RATE_LIMIT = 15
WINDOW_SECONDS = 10
rate_limit_buckets = {}      # client_id -> {"window_start": float, "count": int}


# ----------------------------
# Models
# ----------------------------

class OrderCreate(BaseModel):
    item_name: str


# ----------------------------
# Rate limit dependency
# ----------------------------

def check_rate_limit(
    request: Request,
    response: Response,
    client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Client-Id header is required",
        )

    now = time.time()
    bucket = rate_limit_buckets.get(client_id)

    if bucket is None:
        # First request from this client
        rate_limit_buckets[client_id] = {
            "window_start": now,
            "count": 1,
        }
        return

    elapsed = now - bucket["window_start"]

    if elapsed > WINDOW_SECONDS:
        # Window expired, reset
        bucket["window_start"] = now
        bucket["count"] = 1
        return

    # Window still active
    if bucket["count"] >= RATE_LIMIT:
        retry_after = int(WINDOW_SECONDS - elapsed) + 1
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    bucket["count"] += 1


# ----------------------------
# Idempotent POST /orders
# ----------------------------

@app.post("/orders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_rate_limit)])
def create_order(
    order: OrderCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )

    # If key already seen, return existing order
    if idempotency_key in idempotency_store:
        existing_order_id = idempotency_store[idempotency_key]
        return created_orders[existing_order_id]

    # First time: create new order
    new_id = str(uuid.uuid4())
    new_order = {
        "id": new_id,
        "item_name": order.item_name,
    }

    created_orders[new_id] = new_order
    idempotency_store[idempotency_key] = new_id

    return new_order


# ----------------------------
# Cursor-based pagination: GET /orders
# ----------------------------

@app.get("/orders", dependencies=[Depends(check_rate_limit)])
def list_orders(
    limit: int = Query(10, ge=1),
    cursor: Optional[str] = Query(default=None),
):
    # Determine starting id
    if cursor is None:
        start_id = 1
    else:
        try:
            last_seen_id = int(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor",
            )
        start_id = last_seen_id + 1

    # Collect up to `limit` orders
    items = []
    for order in orders_catalog:
        if order["id"] >= start_id:
            items.append(order)
        if len(items) == limit:
            break

    # Determine next_cursor
    if not items:
        next_cursor = None
    else:
        last_id = items[-1]["id"]
        if last_id >= T:
            next_cursor = None
        else:
            next_cursor = str(last_id)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


# Optional: simple health endpoint
@app.get("/")
def root():
    return {"message": "Orders API running"}