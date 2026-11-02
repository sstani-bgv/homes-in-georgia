"""Durable scheduler rows (`jobs`); the worker executes them (app/worker.py)."""
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Job
from app.services.common import now


def schedule(db: Session, kind: str, ref, due_at: datetime, payload: dict | None = None, replace: bool = True) -> Job:
    """Add a job; replace=True cancels previous undone jobs of the same (kind, ref). Does not commit."""
    if replace:
        cancel(db, kind, ref)
    job = Job(kind=kind, ref=str(ref), due_at=due_at, payload=payload or {})
    db.add(job)
    return job


def cancel(db: Session, kind: str, ref=None, ref_prefix: str | None = None) -> None:
    q = update(Job).where(Job.kind == kind, Job.done_at.is_(None))
    if ref is not None:
        q = q.where(Job.ref == str(ref))
    if ref_prefix is not None:
        q = q.where(Job.ref.like(f"{ref_prefix}%"))
    db.execute(q.values(done_at=now(), last_error="cancelled"))


def pending(db: Session, kind: str | None = None, ref=None) -> list[Job]:
    q = select(Job).where(Job.done_at.is_(None))
    if kind:
        q = q.where(Job.kind == kind)
    if ref is not None:
        q = q.where(Job.ref == str(ref))
    return list(db.scalars(q.order_by(Job.due_at)))


def due(db: Session, limit: int = 50) -> list[Job]:
    """Due undone jobs, row-locked (FOR UPDATE SKIP LOCKED) so several workers never run one job twice."""
    q = (select(Job).where(Job.done_at.is_(None), Job.due_at <= now()).order_by(Job.due_at)
         .limit(limit).with_for_update(skip_locked=True))
    return list(db.scalars(q))
