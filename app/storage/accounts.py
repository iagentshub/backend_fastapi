"""Storage para cuentas de proveedor vinculadas."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class AccountStorage:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, provider: str) -> Path:
        return self._dir / f"{provider}.json"

    def get(self, provider: str) -> Optional[Dict[str, Any]]:
        p = self._path(provider)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.get(provider) or {}
        data["provider"] = provider
        data.setdefault("linked_at", existing.get("linked_at") or _now())
        if not data.get("api_key") and existing.get("api_key"):
            data["api_key"] = existing["api_key"]
        self._path(provider).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return data

    def delete(self, provider: str) -> bool:
        p = self._path(provider)
        if not p.exists():
            return False
        p.unlink()
        return True

    def list(self) -> List[Dict[str, Any]]:
        result = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("api_key"):
                    d["api_key_masked"] = _mask(d["api_key"])
                    del d["api_key"]
                result.append(d)
            except (json.JSONDecodeError, OSError):
                pass
        return result
