"""New Zealand date and number formats (Django ships none for en-NZ).

Day-first dates and a 12-hour clock, as people at the NZ MTC write them:
"17 Oct 2026", "17 Oct 2026, 2:17 PM", "17/10/2026".
"""

DATE_FORMAT = "j M Y"
TIME_FORMAT = "g:i A"
DATETIME_FORMAT = "j M Y, g:i A"
YEAR_MONTH_FORMAT = "F Y"
MONTH_DAY_FORMAT = "j F"
SHORT_DATE_FORMAT = "d/m/Y"
SHORT_DATETIME_FORMAT = "d/m/Y g:i A"
FIRST_DAY_OF_WEEK = 1  # Monday

DATE_INPUT_FORMATS = [
    "%Y-%m-%d",  # 2026-10-17 (ISO, what <input type=date> submits)
    "%d/%m/%Y",  # 17/10/2026
    "%d/%m/%y",  # 17/10/26
    "%d %b %Y",  # 17 Oct 2026
    "%d %B %Y",  # 17 October 2026
]
DATETIME_INPUT_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
]
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
