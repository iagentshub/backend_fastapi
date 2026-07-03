"""flog — logger central de iAgents Hub."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_OK = 25
logging.addLevelName(_OK, "OK")

_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    date     TEXT    NOT NULL,
    time     TEXT    NOT NULL,
    ip       TEXT    NOT NULL DEFAULT '-',
    username TEXT    NOT NULL DEFAULT '-',
    level    TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'BE',
    summary  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_al_ts       ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_al_date     ON app_logs(date);
CREATE INDEX IF NOT EXISTS idx_al_level    ON app_logs(level);
CREATE INDEX IF NOT EXISTS idx_al_username ON app_logs(username);
CREATE INDEX IF NOT EXISTS idx_al_ip       ON app_logs(ip);
CREATE INDEX IF NOT EXISTS idx_al_source   ON app_logs(source);
"""


class _StdoutFmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(7)
        ip = getattr(record, "ip", "-")
        username = getattr(record, "username", "-")
        return f"{ts} - {level} - [{ip}] [{username}] {record.getMessage()}"


class _DBHandler(logging.Handler):
    """Escribe cada entrada de log en logs.sqlite3 de forma síncrona."""

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db = str(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            conn = sqlite3.connect(self._db, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_LOG_SCHEMA)
            conn.close()
        except Exception:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            dt = datetime.fromtimestamp(record.created)
            conn = sqlite3.connect(self._db, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.created,
                    dt.strftime("%Y-%m-%d"),
                    dt.strftime("%H:%M:%S"),
                    getattr(record, "ip", "-") or "-",
                    getattr(record, "username", "-") or "-",
                    record.levelname,
                    getattr(record, "source", "BE") or "BE",
                    record.getMessage(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            self.handleError(record)


def log_db_path() -> Path | None:
    """Devuelve la ruta a logs.sqlite3; None si GAIA_DATA_DIR no está definida."""
    data = os.environ.get("GAIA_DATA_DIR", "").strip()
    if not data:
        return None
    return Path(data) / "logs.sqlite3"


def _build() -> logging.Logger:
    log = logging.getLogger("flog")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_StdoutFmt())
        log.addHandler(h)
        p = log_db_path()
        if p is not None:
            log.addHandler(_DBHandler(p))
    log.propagate = False
    return log


_L = _build()


def debug(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.debug(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def info(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.info(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def ok(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.log(
        _OK,
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def warning(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.warning(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def error(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.error(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )
