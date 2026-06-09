from __future__ import annotations

import re
from datetime import datetime, timezone


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = _SAFE_NAME.sub("-", value)
    value = value.strip(".-")
    return value or "workspace"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
