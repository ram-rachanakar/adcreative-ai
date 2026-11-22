"""DB engine/session. Tenant context is pushed into Postgres (`app.current_org`) on every
transaction so row-level-security policies act as defense in depth behind the repositories."""
import uuid
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(SessionLocal, "after_begin")
def _apply_tenant(session: Session, transaction, connection) -> None:
    org = session.info.get("org_id")
    connection.execute(text("select set_config('app.current_org', :o, true)"), {"o": str(org) if org else ""})


def set_org(session: Session, org_id: uuid.UUID | None) -> None:
    """Bind the session to an organization; re-applies immediately and on every later transaction."""
    session.info["org_id"] = org_id
    session.execute(text("select set_config('app.current_org', :o, true)"), {"o": str(org_id) if org_id else ""})


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
