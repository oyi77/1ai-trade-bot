from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer, Text, Boolean, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    owner_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="tenant", lazy="selectin")
    whitelabel: Mapped[list["TenantWhitelabel"]] = relationship(back_populates="tenant", lazy="selectin")


class TenantWhitelabel(Base):
    __tablename__ = "tenant_whitelabels"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    market: Mapped[str] = mapped_column(String, index=True)  # fx|stocks|crypto|binary|ads|all
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="whitelabel", lazy="selectin")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    plan_code: Mapped[str] = mapped_column(String, index=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String, default="IDR")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    tenant: Mapped["Tenant"] = relationship(back_populates="subscriptions", lazy="selectin")
    payments: Mapped[list["Payment"]] = relationship(back_populates="subscription", lazy="selectin")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    provider: Mapped[str] = mapped_column(String)  # tripay|midtrans|manual
    external_id: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String, default="IDR")
    status: Mapped[str] = mapped_column(String, index=True)
    payment_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    subscription: Mapped["Subscription"] = relationship(back_populates="payments", lazy="selectin")


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    referrer_user_id: Mapped[int] = mapped_column(Integer, index=True)
    ref_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    tier: Mapped[str] = mapped_column(String, index=True)
    commission_rate: Mapped[float] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
