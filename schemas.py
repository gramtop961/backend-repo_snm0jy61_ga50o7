"""
Database Schemas for Vrijstad

Each Pydantic model corresponds to a MongoDB collection (lowercased class name).
Use these models for validation when creating or updating documents.
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

# ---------------------------------
# Core domain models
# ---------------------------------

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    address: Optional[str] = Field(None, description="Primary shipping address")
    is_active: bool = Field(True, description="Whether user is active")


class ProductVariant(BaseModel):
    size: Literal["XS", "S", "M", "L", "XL", "XXL"]
    stock: int = Field(0, ge=0, description="Stock for this size")


class Product(BaseModel):
    name: str = Field(..., description="Product name")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in IDR")
    image: Optional[str] = Field(None, description="Primary image URL")
    category: Optional[str] = Field(None, description="Category like tees, hoodies, outer")
    collection: Optional[str] = Field(None, description="Collection label")
    variants: List[ProductVariant] = Field(default_factory=list, description="Size-level stock")
    tags: List[str] = Field(default_factory=list)
    is_active: bool = Field(True)


class OrderItem(BaseModel):
    product_id: str
    name: str
    image: Optional[str] = None
    size: Optional[str] = None
    quantity: int = Field(..., ge=1)
    price: float = Field(..., ge=0, description="Unit price at purchase time")


class Order(BaseModel):
    user_name: str
    user_email: EmailStr
    address: str
    shipping_method: Literal["standard", "express"] = "standard"
    items: List[OrderItem]
    total_amount: float = Field(..., ge=0)
    payment_status: Literal["pending", "paid", "failed", "canceled"] = "pending"
    created_at: Optional[datetime] = None


class Payment(BaseModel):
    order_id: str
    provider: Literal["midtrans"] = "midtrans"
    transaction_id: Optional[str] = None
    status: Literal["pending", "settlement", "capture", "failed", "expire", "cancel"] = "pending"
    redirect_url: Optional[str] = None
    snap_token: Optional[str] = None


class Post(BaseModel):
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: str
    cover_image: Optional[str] = None
    published: bool = True

