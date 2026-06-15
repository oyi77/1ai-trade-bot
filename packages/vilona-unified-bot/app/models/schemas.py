from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str
    owner_user_id: int
    active: bool

    class Config:
        from_attributes = True


class TenantWhitelabelOut(BaseModel):
    id: int
    tenant_id: int
    market: Literal["fx", "stocks", "crypto", "binary", "ads", "all"]
    enabled: bool

    class Config:
        from_attributes = True


class SubscriptionOut(BaseModel):
    id: int
    tenant_id: int
    telegram_user_id: int
    plan_code: str
    price: float
    currency: str = "IDR"
    active: bool
    started_at: datetime
    expires_at: datetime

    class Config:
        from_attributes = True


class PaymentIn(BaseModel):
    provider: Literal["tripay", "midtrans", "manual"]
    external_id: str
    amount: float
    currency: str = "IDR"
    status: Literal["paid", "pending", "failed", "expired"]
    metadata: Optional[str] = None


class PaymentOut(BaseModel):
    id: int
    subscription_id: int
    provider: str
    external_id: str
    amount: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
