"""
Value normalization shared by every tabular comparison.

BI tools and Excel disagree on *formatting* far more often than they
disagree on *numbers* — "£3,615,900", "3.6M", "(1,200)", "12.5%" and
3615900.0 all need to meet in the middle before a difference means
anything. Everything that compares two cells goes through here.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

_CURRENCY_SYMBOLS = "£$€¥₹₩₽¢"
_CURRENCY_CODES = re.compile(r"^(GBP|USD|EUR|INR|AUD|CAD|NZD|CHF|JPY|SGD|ZAR|NOK|SEK|DKK)\s*|\s*(GBP|USD|EUR|INR|AUD|CAD|NZD|CHF|JPY|SGD|ZAR|NOK|SEK|DKK)$", re.I)
_SUFFIXES = {"k": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12}
_NUMBER = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?(k|m|mn|mm|b|bn|t)?$", re.I)
_NULL_TOKENS = {"", "-", "—", "–", "n/a", "na", "null", "none", "nan", "#n/a", "(blank)", "blank", "#div/0!", "#value!"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_INTEGRAL_FLOAT_STR = re.compile(r"^-?\d+\.0+$")

_MONTHS = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april", "may": "may", "jun": "june",
    "jul": "july", "aug": "august", "sep": "september", "sept": "september", "oct": "october",
    "nov": "november", "dec": "december",
}
_MONTH_NAMES = set(_MONTHS.values())


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_TOKENS:
        return True
    return False


def parse_number(value: Any) -> Optional[float]:
    """Best-effort numeric parse. Returns None when the value isn't a number
    (never raises) so callers can count what failed instead of crashing."""
    if value is None or isinstance(value, (datetime, date, time)):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, Decimal)):
        return float(value)
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if hasattr(value, "item") and not isinstance(value, str):  # numpy scalars
        try:
            return parse_number(value.item())
        except (TypeError, ValueError):
            return None

    s = str(value).strip()
    for space in (" ", " ", " "):  # no-break / narrow (French thousands) / thin spaces
        s = s.replace(space, " ")
    if s.lower() in _NULL_TOKENS:
        return None
    for dash in ("−", "‒", "–", "—"):  # unicode minus signs
        s = s.replace(dash, "-")

    # Peel signs, brackets, currency and CR/DR in any order: "(1,200)",
    # "£(1,234)", "-(1,000)", "£-1,000", "1,234-", "1,234 CR". Any one
    # negative marker makes the number negative.
    negative = False
    for _ in range(4):
        before = s
        s = _CURRENCY_CODES.sub("", s.strip(_CURRENCY_SYMBOLS + " ")).strip(_CURRENCY_SYMBOLS + " ")
        if s.startswith("(") and s.endswith(")"):
            negative, s = True, s[1:-1].strip()
        if len(s) > 1 and s.endswith("-"):
            negative, s = True, s[:-1].strip()
        if s[:1] in "+-":
            negative, s = negative or s[0] == "-", s[1:].strip()
        marker = re.search(r"\s*\b(CR|DR)$", s, re.I)
        if marker:
            negative, s = negative or marker.group(1).upper() == "CR", s[: marker.start()].strip()
        if s == before:
            break

    percent = s.endswith("%")
    if percent:
        s = s[:-1].strip()
    s = _separators(s.replace(" ", "").replace("'", "").replace("_", ""))

    match = _NUMBER.match(s)
    if not match:
        return None
    if match.group(2) and abs(int(match.group(2))) > 300:  # 1e99999999 would hang / overflow
        return None
    try:
        number = float(match.group(1) + (f"e{match.group(2)}" if match.group(2) else ""))
    except (OverflowError, ValueError):
        return None
    if match.group(3):
        number *= _SUFFIXES[match.group(3).lower()]
    if percent:
        number /= 100.0
    if not math.isfinite(number):
        return None
    return -number if negative else number


def _separators(s: str) -> str:
    """Resolve thousands vs decimal separators: '1,234.56' and '1.234,56'
    (European) both -> '1234.56'; '1,5' -> '1.5'; '1,234' and '1.234.567'
    are thousands. A lone '1.234' stays a decimal — ambiguous without a
    locale, and the English reading is the common one in BI exports."""
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            return s.replace(".", "").replace(",", ".")
        return s.replace(",", "")
    if "," in s:
        parts = s.split(",")
        if len(parts) == 2 and (len(re.sub(r"\D.*$", "", parts[1])) != 3 or parts[0] in ("", "0")):
            return f"{parts[0]}.{parts[1]}"
        return s.replace(",", "")
    if s.count(".") > 1:
        return s.replace(".", "")
    return s


def normalize_key(value: Any, case_insensitive: bool = True) -> str:
    """Canonical string form of a key cell, so 2025 / 2025.0 / "2025",
    " Pump " / "pump", "Mar" / "March" and datetime(2026,3,1) / "2026-03-01"
    all line up on a join."""
    if is_blank(value):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Decimal):
        value = float(value)  # Decimal("2025.0") must meet 2025
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, int):
        return str(value)

    s = " ".join(str(value).split())
    if _INTEGRAL_FLOAT_STR.match(s):
        s = s.split(".")[0]
    elif _ISO_DATE.match(s):
        try:
            parsed = datetime.fromisoformat(s.replace(" ", "T"))
            return parsed.date().isoformat() if parsed.time() == time(0) else parsed.isoformat()
        except ValueError:
            pass
    lowered = s.lower()
    if lowered in _MONTHS:
        return _MONTHS[lowered]
    if lowered in _MONTH_NAMES:
        return lowered
    return s.casefold() if case_insensitive else s


def loose_key(value: Any) -> str:
    """Aggressive normalization used only to *diagnose* key-format problems
    (leading zeros, punctuation, spacing) — never for the actual join."""
    s = normalize_key(value)
    s = re.sub(r"[^0-9a-z]", "", s.casefold())
    return s.lstrip("0") or s


def infer_dtype(values: list[Any]) -> str:
    """'number' | 'date' | 'bool' | 'text' from a sample of values."""
    sample = [v for v in values if not is_blank(v)][:500]
    if not sample:
        return "text"
    if all(isinstance(v, bool) for v in sample):
        return "bool"
    if all(isinstance(v, (datetime, date)) for v in sample):
        return "date"
    date_like = sum(1 for v in sample if isinstance(v, (datetime, date)) or (isinstance(v, str) and _ISO_DATE.match(v.strip())))
    if date_like / len(sample) >= 0.9:
        return "date"
    numeric = sum(1 for v in sample if parse_number(v) is not None)
    if numeric / len(sample) >= 0.9:
        return "number"
    return "text"


def to_jsonable(value: Any) -> Any:
    """Cell value -> something JSON/SQLite can store without losing meaning."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item") and not isinstance(value, str):
        try:
            return to_jsonable(value.item())
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
