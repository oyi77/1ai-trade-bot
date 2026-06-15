# Unified Bot schema bootstrap
# Tables: tenant / subscription / referral / payment / whitelabel

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
)

LOG = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64), default="active")

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    referrals: Mapped[list["Referral"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    brand_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tenant.brand_id"), nullable=True)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="subscriptions")


class Referral(Base):
    __tablename__ = "referral"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[str] = mapped_column(String(128), index=True)
    referee_id: Mapped[str] = mapped_column(String(128), index=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    brand_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tenant.brand_id"), nullable=True)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="referrals")


class Payment(Base):
    __tablename__ = "payment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    brand_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tenant.brand_id"), nullable=True)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="payments")


class Whitelabel(Base):
    __tablename__ = "whitelabel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def get_engine(db_path: str | Path = "data/unified_bot.db"):
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{p.as_posix()}", echo=False)
    return engine


def init_db(engine=None, db_path: str | Path = "data/unified_bot.db") -> Session:
    engine = engine or get_engine(db_path)
    Base.metadata.create_all(engine)
    return Session(engine)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("DB initialized")
