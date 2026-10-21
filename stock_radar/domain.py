"""Shared primitives: Taipei time and the numeric/timestamp parsing every fetcher relies on."""
from datetime import datetime, timedelta, timezone
import math

TW = timezone(timedelta(hours=8))


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(str(value).replace(',', '').strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def positive(value):
    n = number(value)
    return n if n is not None and n > 0 else None


def stamp(value):
    try:
        d = datetime.fromisoformat(value)
        return d.astimezone(TW) if d.tzinfo else None
    except (TypeError, ValueError):
        return None
