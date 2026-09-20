from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.sql import func
from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    severity = Column(String(16), nullable=False)
    title = Column(String(256), nullable=False)
    resource = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="open")
    diagnosis = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Remediation(Base):
    __tablename__ = "remediations"
    id = Column(Integer, primary_key=True)
    alert_id = Column(Integer, nullable=False, index=True)
    action_key = Column(String(64), nullable=False)
    action_title = Column(String(256), nullable=False)
    rationale = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending_approval")
    requested_by = Column(String(128), nullable=False)
    approved_by = Column(String(128), nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    executed_at = Column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor = Column(String(128), nullable=False)
    action = Column(String(128), nullable=False)
    target = Column(String(256), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DatabaseInstance(Base):
    __tablename__ = "database_instances"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    engine = Column(String(64), nullable=False)
    service = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    healthy = Column(Boolean, default=True, nullable=False)
    utilization = Column(Integer, default=0, nullable=False)
    connections = Column(Integer, default=0, nullable=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
