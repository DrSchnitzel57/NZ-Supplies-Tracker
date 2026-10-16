"""Display helpers shared by every template (registered as builtins).

Plain-language dates ("Today, 2:17 PM"), item labels that always include the
size, and staff/missionary names the way MTC staff say them.
"""

from __future__ import annotations

import datetime as _dt
import re

from django import template
from django.utils import formats, timezone

from apps.common.sorting import natural_key

register = template.Library()


@register.filter
def when(value):
    """Relative, NZ-local timestamp: Today / Yesterday / weekday / full date."""
    if not value:
        return ""
    if isinstance(value, _dt.datetime):
        local = timezone.localtime(value) if timezone.is_aware(value) else value
        today = timezone.localdate()
        clock = formats.time_format(local, "g:i A")
        days = (today - local.date()).days
        if days == 0:
            return f"Today, {clock}"
        if days == 1:
            return f"Yesterday, {clock}"
        if 1 < days < 7:
            return f"{formats.date_format(local, 'D j M')}, {clock}"
        if local.year == today.year:
            return f"{formats.date_format(local, 'j M')}, {clock}"
        return formats.date_format(local, "j M Y")
    if isinstance(value, _dt.date):
        return day(value)
    return value


@register.filter
def day(value):
    """A date as "Thu 29 Oct" this year, otherwise "29 Oct 2027"."""
    if not value:
        return ""
    today = timezone.localdate()
    if value.year == today.year:
        return formats.date_format(value, "D j M")
    return formats.date_format(value, "j M Y")


@register.filter
def days_until(value):
    """Whole days from today until a date (negative when past)."""
    if not value:
        return None
    return (value - timezone.localdate()).days


@register.filter
def relative_day(value):
    """'today', 'tomorrow', 'in 5 days', '3 days ago'."""
    delta = days_until(value)
    if delta is None:
        return ""
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if delta > 1:
        return f"in {delta} days"
    return f"{-delta} days ago"


@register.filter
def line_label(line):
    """An issue line as "White Shirts (38 L)" — sizes are never dropped."""
    name = getattr(line, "item_name", "") or ""
    size = getattr(line, "item_size", "") or ""
    return f"{name} ({size})" if size else name


@register.filter
def staff_name(issue_or_user, which="issued"):
    """Who did it, using the staff display name when the account still exists."""
    obj = issue_or_user
    if hasattr(obj, "shown_name"):
        return obj.shown_name
    user = getattr(obj, f"{which}_by", None)
    snapshot = getattr(obj, f"{which}_by_username", "") or ""
    if user is not None and getattr(user, "display_name", ""):
        return user.display_name
    return snapshot or (getattr(user, "username", "") if user else "")


@register.filter
def person_name(issue):
    """Recipient of an issue as staff say it ("Sister Halvorsen")."""
    person = getattr(issue, "missionary", None)
    if person is not None:
        return person.display_name
    return getattr(issue, "missionary_name", "")


@register.filter
def initials(text):
    words = [w for w in re.split(r"[\s,]+", str(text or "")) if w and w[0].isalpha()]
    return "".join(w[0] for w in words[:2]).upper()


@register.filter
def percent(part, whole):
    try:
        part, whole = float(part or 0), float(whole or 0)
    except (TypeError, ValueError):
        return 0
    if whole <= 0:
        return 0
    return max(0, min(100, round(part / whole * 100)))


@register.filter
def plural(count, forms="s"):
    """{{ n|plural }} -> "s"; {{ n|plural:"y,ies" }} -> "y"/"ies"."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if "," in forms:
        one, many = forms.split(",", 1)
    else:
        one, many = "", forms
    return one if count == 1 else many


@register.simple_tag
def sort_sizes(items):
    return sorted(items, key=lambda i: natural_key(getattr(i, "size", "")))
