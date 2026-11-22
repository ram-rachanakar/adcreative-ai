"""Org-scoped repository: every query is filtered by organization_id and soft-deletes are hidden."""
import uuid
from datetime import datetime, timezone
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


class OrgRepo(Generic[T]):
    def __init__(self, db: Session, model: type[T], org_id: uuid.UUID):
        self.db, self.model, self.org_id = db, model, org_id

    def _q(self):
        return select(self.model).where(self.model.organization_id == self.org_id, self.model.deleted_at.is_(None))

    def get(self, id: uuid.UUID) -> T | None:
        return self.db.scalar(self._q().where(self.model.id == id))

    def list(self, **filters) -> list[T]:
        q = self._q().order_by(self.model.created_at.desc())
        for k, v in filters.items():
            q = q.where(getattr(self.model, k) == v)
        return list(self.db.scalars(q))

    def add(self, **fields) -> T:
        obj = self.model(organization_id=self.org_id, **fields)
        self.db.add(obj)
        self.db.flush()
        return obj

    def soft_delete(self, obj: T) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
