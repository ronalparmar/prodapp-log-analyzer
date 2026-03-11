"""
SQLAlchemy models and database setup for the log analyzer.
Uses SQLite by default (configurable via DATABASE_URL env var).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/log_analyzer.db")


class Base(DeclarativeBase):
    pass


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(512), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    file_size = Column(Integer, nullable=True)

    log_files = relationship("LogFile", back_populates="upload", cascade="all, delete-orphan")


class LogFile(Base):
    __tablename__ = "log_files"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("uploads.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(512), nullable=False)

    # Metadata extracted from file
    device_id = Column(String(64), nullable=True, index=True)
    package_name = Column(String(255), nullable=True)
    environment = Column(String(16), nullable=True)
    username = Column(String(255), nullable=True)
    parsed_at = Column(DateTime, default=datetime.utcnow)

    upload = relationship("Upload", back_populates="log_files")
    app_versions = relationship("AppVersion", back_populates="log_file", cascade="all, delete-orphan")
    emails = relationship("UserEmail", back_populates="log_file", cascade="all, delete-orphan")
    scan_events = relationship("ScanEvent", back_populates="log_file", cascade="all, delete-orphan")
    exception_events = relationship("ExceptionEvent", back_populates="log_file", cascade="all, delete-orphan")


class AppVersion(Base):
    __tablename__ = "app_versions"

    id = Column(Integer, primary_key=True, index=True)
    log_file_id = Column(Integer, ForeignKey("log_files.id"), nullable=False)
    version = Column(String(64), nullable=False)

    log_file = relationship("LogFile", back_populates="app_versions")


class UserEmail(Base):
    __tablename__ = "user_emails"

    id = Column(Integer, primary_key=True, index=True)
    log_file_id = Column(Integer, ForeignKey("log_files.id"), nullable=False)
    email = Column(String(255), nullable=False)

    log_file = relationship("LogFile", back_populates="emails")


class ScanEvent(Base):
    __tablename__ = "scan_events"

    id = Column(Integer, primary_key=True, index=True)
    log_file_id = Column(Integer, ForeignKey("log_files.id"), nullable=False)
    timestamp = Column(DateTime, nullable=True)
    raw_ts = Column(String(32), nullable=True)
    item_number = Column(String(128), nullable=False, index=True)
    barcode_format = Column(String(64), nullable=True)
    entry_mode = Column(String(16), nullable=True)   # "scan" or "manual"
    process = Column(String(128), nullable=True)
    line_number = Column(Integer, nullable=True)

    log_file = relationship("LogFile", back_populates="scan_events")


class ExceptionEvent(Base):
    __tablename__ = "exception_events"

    id = Column(Integer, primary_key=True, index=True)
    log_file_id = Column(Integer, ForeignKey("log_files.id"), nullable=False)
    timestamp = Column(DateTime, nullable=True)
    raw_ts = Column(String(32), nullable=True)
    exception_type = Column(String(512), nullable=True, index=True)
    message = Column(Text, nullable=True)
    context_text = Column(Text, nullable=True)
    line_number = Column(Integer, nullable=True)

    log_file = relationship("LogFile", back_populates="exception_events")


# ---------------------------------------------------------------------------
# Engine / session factory
# ---------------------------------------------------------------------------

def get_engine(url: str = DATABASE_URL):
    os.makedirs(os.path.dirname(os.path.abspath(url.replace("sqlite:///", ""))), exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(engine=None):
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return engine


def get_session_factory(engine=None):
    if engine is None:
        engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
