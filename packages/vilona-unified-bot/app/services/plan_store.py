"""Plan store with Pydantic v2 validation and SQLAlchemy repository.

Keyed by variant_id (Scalev).  Plans are seeded from SCALEV_PLAN_VARIANT_IDS_JSON
environment variable.

Example env:
  SCALEV_PLAN_VARIANT_IDS_JSON={"AI Trader":527961,"Suami Perkasa":479490,"Basic":530093,"Pro":530094}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

LOG = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class PlanRecord(Base):
    __tablename__ = "plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    variant_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    price_idr: Mapped[int] = mapped_column(Integer, default=0)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(32), default="")

    def __repr__(self):
        return f"PlanRecord(key={self.plan_key}, variant={self.variant_id})"


class PlanModel(BaseModel):
    plan_key: str
    variant_id: int
    price_idr: int = 0
    duration_days: int = 30
    is_active: bool = True


class PlanRepository:
    DEFAULT_DB_PATH: ClassVar[str] = "data/scalev_plans.db"

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.getenv(
            "SCALEV_PLAN_DB_PATH", self.DEFAULT_DB_PATH
        )
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(self.engine)
        LOG.debug("PlanRepository connected: %s", self.db_path)

    def seed_from_env(self) -> int:
        raw = os.getenv("SCALEV_PLAN_VARIANT_IDS_JSON", "") or os.getenv(
            "PLAN_VARIANT_IDS_JSON", ""
        )
        if not raw:
            _seed_defaults(self)
            return _count_active(self)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            LOG.warning("invalid env JSON: %s", exc)
            _seed_defaults(self)
            return _count_active(self)

        count = 0
        with Session(self.engine) as sess:
            with sess.no_autoflush:
                existing_keys = {
                    r[0] for r in sess.execute(
                        select(PlanRecord.plan_key)
                    ).all()
                }
            for plan_name, variant_id in data.items():
                plan_key = (plan_name or "").strip().lower().replace(" ", "_")
                if not plan_key or not variant_id or plan_key in existing_keys:
                    continue
                rec = PlanRecord(
                    plan_key=plan_key,
                    variant_id=int(variant_id),
                    price_idr=_DEFAULT_PRICES.get(plan_key, 0),
                    duration_days=_DEFAULT_DURATIONS.get(plan_key, 30),
                    created_at=datetime.now().isoformat(),
                )
                sess.add(rec)
                existing_keys.add(plan_key)
                count += 1
            sess.commit()
        LOG.info("seeded %d plans from env", count)
        return count

    def get_by_variant(self, variant_id: int) -> Optional[PlanModel]:
        with Session(self.engine) as sess:
            rec = sess.execute(
                select(PlanRecord).where(PlanRecord.variant_id == variant_id)
            ).scalar_one_or_none()
            if rec is None:
                return None
            return PlanModel(
                plan_key=rec.plan_key,
                variant_id=rec.variant_id,
                price_idr=rec.price_idr,
                duration_days=rec.duration_days,
                is_active=bool(rec.is_active),
            )

    def get_by_key(self, plan_key: str) -> Optional[PlanModel]:
        with Session(self.engine) as sess:
            rec = sess.get(PlanRecord, plan_key)
            if rec is None:
                return None
            return PlanModel(
                plan_key=rec.plan_key,
                variant_id=rec.variant_id,
                price_idr=rec.price_idr,
                duration_days=rec.duration_days,
                is_active=bool(rec.is_active),
            )

    def all_active(self) -> list[PlanModel]:
        with Session(self.engine) as sess:
            rows = sess.execute(
                select(PlanRecord).where(PlanRecord.is_active == 1)
            ).scalars().all()
            return [
                PlanModel(
                    plan_key=r.plan_key,
                    variant_id=r.variant_id,
                    price_idr=r.price_idr,
                    duration_days=r.duration_days,
                    is_active=True,
                )
                for r in rows
            ]


_DEFAULT_PRICES = {"basic": 99000, "ai_trader": 99000, "pro": 77000, "suami_perkasa": 77000}
_DEFAULT_DURATIONS = {"basic": 30, "ai_trader": 30, "pro": 30, "suami_perkasa": 30}


def _seed_defaults(repo: PlanRepository) -> None:
    defaults = [
        ("ai_trader", 527961, 99000, 30),
        ("suami_perkasa", 479490, 77000, 30),
        ("basic", 530093, 99000, 30),
        ("pro", 530094, 77000, 30),
    ]
    with Session(repo.engine) as sess:
        with sess.no_autoflush:
            existing_keys = {
                r[0] for r in sess.execute(
                    select(PlanRecord.plan_key)
                ).all()
            }
        for plan_key, variant_id, price_idr, duration_days in defaults:
            if plan_key in existing_keys:
                continue
            rec = PlanRecord(
                plan_key=plan_key,
                variant_id=variant_id,
                price_idr=price_idr,
                duration_days=duration_days,
                created_at=datetime.now().isoformat(),
            )
            sess.add(rec)
            existing_keys.add(plan_key)
        sess.commit()


def _count_active(repo: PlanRepository) -> int:
    with Session(repo.engine) as sess:
        rows = sess.execute(
            select(PlanRecord.id).where(PlanRecord.is_active == 1)
        ).scalars().all()
        return len(rows)


repo = PlanRepository()
