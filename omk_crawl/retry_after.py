"""Parse HTTP Retry-After without letting untrusted headers create unbounded waits."""

from __future__ import annotations

import math
import sys
import time
from datetime import timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(headers: dict[str, str]) -> float | None:
    value = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    if value is None:
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            # Saturate enormous delta-seconds so callers stop, rather than retry early.
            return min(float(value), sys.float_info.max)
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        delay = date.timestamp() - time.time()
    except (ValueError, TypeError, OverflowError):
        return None
    return max(0.0, delay) if math.isfinite(delay) else None
