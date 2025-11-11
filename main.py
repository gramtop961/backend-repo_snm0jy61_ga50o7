import os
import base64
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta, timezone

from database import db, create_document, get_documents
from schemas import Product, Order, OrderItem, Payment

from bson import ObjectId
import requests

app = FastAPI(title="Vrijstad API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Utilities

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id")) if doc.get("_id") else None
    # Convert datetime fields
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


@app.get("/")
def read_root():
    return {"brand": "Vrijstad", "message": "API is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
            response["database"] = "✅ Connected & Working"
    except Exception as e:
        response["database"] = f"⚠️  Connected but Error: {str(e)[:80]}"
    return response


# -------------------------------
# Products
# -------------------------------

@app.get("/api/products")
def list_products(
    q: Optional[str] = None,
    category: Optional[str] = None,
    size: Optional[str] = None,
    collection: Optional[str] = None,
    page: int = 1,
    limit: int = 12,
):
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 12

    filter_query: Dict[str, Any] = {"is_active": True}
    if q:
        filter_query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"tags": {"$regex": q, "$options": "i"}},
        ]
    if category:
        filter_query["category"] = category
    if collection:
        filter_query["collection"] = collection
    if size:
        filter_query["variants.size"] = size

    total = db["product"].count_documents(filter_query)
    cursor = (
        db["product"].find(filter_query).skip((page - 1) * limit).limit(limit)
    )
    items = [serialize(doc) for doc in list(cursor)]
    return {"items": items, "page": page, "limit": limit, "total": total}


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    doc = db["product"].find_one({"_id": oid(product_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize(doc)


class SeedRequest(BaseModel):
    with_demo: bool = True


@app.post("/api/seed")
def seed_products(_: SeedRequest):
    count = db["product"].count_documents({})
    if count > 0:
        return {"message": "Already seeded", "count": count}

    demo_products = [
        Product(
            name="Bandung Crest Tee",
            description="Oversized tee with Vrijstad crest. 240gsm cotton.",
            price=249000,
            image="https://images.unsplash.com/photo-1542291026-7eec264c27ff?q=80&w=1600&auto=format&fit=crop",
            category="tees",
            collection="AW24",
            variants=[
                {"size": "S", "stock": 20},
                {"size": "M", "stock": 20},
                {"size": "L", "stock": 20},
                {"size": "XL", "stock": 10},
            ],
            tags=["crest", "oversized"],
        ),
        Product(
            name="Freedom Hoodie",
            description="Heavyweight hoodie with embroidery.",
            price=549000,
            image="https://images.unsplash.com/photo-1516826957135-700dedea698c?q=80&w=1600&auto=format&fit=crop",
            category="hoodies",
            collection="AW24",
            variants=[
                {"size": "M", "stock": 15},
                {"size": "L", "stock": 15},
                {"size": "XL", "stock": 8},
            ],
            tags=["hoodie", "embroidery"],
        ),
        Product(
            name="Motion Cargo Pant",
            description="Relaxed fit cargo with tapered leg.",
            price=499000,
            image="https://images.unsplash.com/photo-1519741497674-611481863552?q=80&w=1600&auto=format&fit=crop",
            category="bottoms",
            collection="Core",
            variants=[
                {"size": "S", "stock": 10},
                {"size": "M", "stock": 12},
                {"size": "L", "stock": 10},
            ],
            tags=["cargo", "street"],
        ),
    ]

    ids = []
    for p in demo_products:
        pid = create_document("product", p)
        ids.append(pid)

    return {"message": "Seeded", "count": len(ids), "ids": ids}


# -------------------------------
# Orders and Payments (Midtrans)
# -------------------------------

class CheckoutItem(BaseModel):
    product_id: str
    size: Optional[str] = None
    quantity: int


class CheckoutRequest(BaseModel):
    name: str
    email: EmailStr
    address: str
    shipping_method: str = "standard"
    items: List[CheckoutItem]


def compute_totals(items: List[CheckoutItem]):
    product_ids = [oid(i.product_id) for i in items]
    products_map: Dict[str, Dict[str, Any]] = {}
    for doc in db["product"].find({"_id": {"$in": product_ids}}):
        products_map[str(doc["_id"]) ] = doc

    order_items: List[OrderItem] = []
    total = 0.0
    for ci in items:
        doc = products_map.get(ci.product_id)
        if not doc:
            raise HTTPException(status_code=400, detail=f"Product {ci.product_id} not found")
        price = float(doc.get("price", 0))
        total += price * ci.quantity
        order_items.append(
            OrderItem(
                product_id=ci.product_id,
                name=doc.get("name"),
                image=doc.get("image"),
                size=ci.size,
                quantity=ci.quantity,
                price=price,
            )
        )
    return order_items, total


def midtrans_auth_header() -> Dict[str, str]:
    server_key = os.getenv("MIDTRANS_SERVER_KEY", "")
    if not server_key:
        return {}
    token = base64.b64encode(f"{server_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


@app.post("/api/checkout")
def create_order(payload: CheckoutRequest):
    order_items, computed_total = compute_totals(payload.items)
    order_doc = Order(
        user_name=payload.name,
        user_email=payload.email,
        address=payload.address,
        shipping_method=payload.shipping_method if payload.shipping_method in ["standard", "express"] else "standard",
        items=order_items,
        total_amount=computed_total,
    )
    order_id = create_document("order", order_doc)

    # Prepare Midtrans transaction (Snap)
    headers = midtrans_auth_header()
    snap_url = "https://app.sandbox.midtrans.com/snap/v1/transactions"

    redirect_url = None
    snap_token = None
    transaction_id = None

    if headers:
        item_details = [
            {
                "id": it.product_id,
                "price": int(it.price),
                "quantity": it.quantity,
                "name": it.name[:50],
            }
            for it in order_items
        ]
        payload_mid = {
            "transaction_details": {
                "order_id": order_id,
                "gross_amount": int(computed_total),
            },
            "item_details": item_details,
            "customer_details": {
                "first_name": payload.name,
                "email": payload.email,
                "billing_address": {"address": payload.address},
                "shipping_address": {"address": payload.address},
            },
            "credit_card": {"secure": True},
            "callbacks": {
                "finish": os.getenv("FRONTEND_URL", "") + "/checkout/success",
            },
            "expiry": {
                "start_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %z"),
                "unit": "hour",
                "duration": 24,
            },
        }
        try:
            res = requests.post(snap_url, json=payload_mid, headers=headers, timeout=20)
            data = res.json()
            snap_token = data.get("token")
            redirect_url = data.get("redirect_url")
            transaction_id = data.get("transaction_id") or data.get("token")
        except Exception as e:
            # Continue without payment link in case of env not configured
            redirect_url = None

    pay_doc = Payment(
        order_id=order_id,
        transaction_id=transaction_id,
        redirect_url=redirect_url,
        snap_token=snap_token,
    )
    create_document("payment", pay_doc)

    return {
        "order_id": order_id,
        "total_amount": int(computed_total),
        "payment": {
            "provider": "midtrans",
            "redirect_url": redirect_url,
            "snap_token": snap_token,
        },
    }


@app.post("/api/midtrans/webhook")
async def midtrans_webhook(request: Request):
    body = await request.json()
    order_id = body.get("order_id")
    transaction_status = body.get("transaction_status")

    # Map Midtrans status to our payment_status
    status_map = {
        "settlement": "paid",
        "capture": "paid",
        "pending": "pending",
        "deny": "failed",
        "cancel": "canceled",
        "expire": "failed",
        "failure": "failed",
    }
    new_status = status_map.get(transaction_status, "pending")

    if order_id:
        db["order"].update_one({"_id": oid(order_id)}, {"$set": {"payment_status": new_status, "updated_at": datetime.now(timezone.utc)}})
        db["payment"].update_many({"order_id": order_id}, {"$set": {"status": transaction_status, "updated_at": datetime.now(timezone.utc)}})

    return {"received": True}


# Simple posts (journal) listing for MVP
@app.get("/api/posts")
def list_posts():
    posts = [serialize(p) for p in db["post"].find({"published": True}).limit(20)]
    return {"items": posts}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
