"""flog — logger central de iAgents Hub."""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

_OK = 25
logging.addLevelName(_OK, "OK")


class _Fmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from datetime import datetime
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(7)
        return f"{ts} - {level} - {record.getMessage()}"


class _DateFileHandler(logging.Handler):
    """Escribe en <log_dir>/YYYYMMDD.log; abre un nuevo fichero cada día."""

    def __init__(self, log_dir: Path) -> None:
        super().__init__()
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._date: str = ""
        self._stream = None
        self._rotate()

    def _today(self) -> str:
        from datetime import date
        return date.today().strftime("%Y%m%d")

    def _rotate(self) -> None:
        today = self._today()
        if self._date == today:
            return
        if self._stream:
            try:
                self._stream.close()
            except OSError:
                pass
        self._date = today
        self._stream = (self._log_dir / f"{today}.log").open("a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        self._rotate()
        try:
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream:
            try:
                self._stream.close()
            except OSError:
                pass
        super().close()


def _log_dir() -> Path | None:
    """Devuelve data/logs/ solo si GAIA_DATA_DIR está definida."""
    data = os.environ.get("GAIA_DATA_DIR", "").strip()
    if not data:
        return None
    d = Path(data) / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _build() -> logging.Logger:
    log = logging.getLogger("flog")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_Fmt())
        log.addHandler(h)
        d = _log_dir()
        if d is not None:
            fh = _DateFileHandler(d)
            fh.setFormatter(_Fmt())
            log.addHandler(fh)
    log.propagate = False
    return log


_L = _build()


def debug(msg: str, *a, **kw)   -> None: _L.debug(msg, *a, **kw)
def info(msg: str, *a, **kw)    -> None: _L.info(msg, *a, **kw)
def ok(msg: str, *a, **kw)      -> None: _L.log(_OK, msg, *a, **kw)
def warning(msg: str, *a, **kw) -> None: _L.warning(msg, *a, **kw)
def error(msg: str, *a, **kw)   -> None: _L.error(msg, *a, **kw)
