"""Money is integer paise everywhere inside Settle; rupees only at the edges."""

from decimal import Decimal, InvalidOperation


def to_paise(value) -> int:
    """'1,234.50' / '₹1234.5' / 1234.5 -> 123450. Blank -> 0."""
    if value is None:
        return 0
    text = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    if text in ("", "-"):
        return 0
    try:
        return int((Decimal(text) * 100).quantize(Decimal(1)))
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {value!r}") from e


def rupees(paise: int) -> str:
    """123456789 -> '₹12,34,567.89' (Indian grouping)."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}.{frac:02d}"
