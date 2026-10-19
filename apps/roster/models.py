import re
import uuid

from django.db import models

ARRIVED = "arrived"
EXPECTED = "expected"
ARRIVAL_CHOICES = [("", "Not recorded"), (ARRIVED, "Arrived"), (EXPECTED, "Not yet arrived")]
_ARRIVED_WORDS = {"true", "yes", "y", "1", "arrived", "in", "in-house", "inhouse",
                  "in house", "here", "present"}
_EXPECTED_WORDS = {"false", "no", "n", "0", "expected", "awaiting", "not arrived",
                   "not yet arrived", "pending", "out"}


def normalize_arrival(value) -> str:
    """Map the many spellings of "has this missionary arrived?" to one value.

    The original HTML stored a JSON boolean (rendered as "True"/"False"),
    spreadsheets say yes/no or In-house/Awaiting. Known words become
    ``arrived``/``expected``; unknown text is kept verbatim (never lost).
    """
    if value is True:
        return ARRIVED
    if value is False:
        return EXPECTED
    text = str(value or "").strip()
    lowered = re.sub(r"\s+", " ", text.lower())
    if lowered in _ARRIVED_WORDS:
        return ARRIVED
    if lowered in _EXPECTED_WORDS:
        return EXPECTED
    return text


def _tidy_case(text: str) -> str:
    """Title-case names that arrived in ALL CAPS; leave mixed case alone."""
    if text and text == text.upper() and any(c.isalpha() for c in text):
        return text.title()
    return text


class Missionary(models.Model):
    """Roster entry with a stable internal ID.

    external_id is the operator-visible unique string. History rows snapshot
    name/external_id at write time, so renames never rewrite past issues.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default="")
    # Source-preserved roster context (converted from the approved seed and
    # carried through bundles; blank when the source did not provide them).
    title_prefix = models.CharField(max_length=16, blank=True, default="")  # Elder/Sister
    arrival_status = models.CharField(max_length=32, blank=True, default="")
    stake = models.CharField(max_length=128, blank=True, default="")
    mission = models.CharField(max_length=128, blank=True, default="")
    language = models.CharField(max_length=64, blank=True, default="")
    arrival_date = models.DateField(null=True, blank=True)
    # The day the missionary leaves the MTC for the field (the original
    # HTML's "missionArrival"). Drives "leaving soon" on the overview.
    departure_date = models.DateField(null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    personal_email = models.EmailField(blank=True, default="")
    intake_metadata = models.JSONField(default=dict, blank=True)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.arrival_status = normalize_arrival(self.arrival_status)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.external_id})"

    # ---- display helpers (never stored) ----
    @property
    def arrived(self):
        status = normalize_arrival(self.arrival_status)
        if status == ARRIVED:
            return True
        if status == EXPECTED:
            return False
        return None

    @property
    def arrival_label(self) -> str:
        status = normalize_arrival(self.arrival_status)
        return dict(ARRIVAL_CHOICES).get(status, status) or "Not recorded"

    @property
    def surname(self) -> str:
        """Family name from a "Family, Given" record (or the last word)."""
        name = (self.name or "").strip()
        if "," in name:
            family = name.split(",", 1)[0].strip()
        else:
            family = name.rsplit(" ", 1)[-1] if name else ""
        return _tidy_case(family)

    @property
    def full_name(self) -> str:
        """Given names first: "Halvorsen, Margot Louise" -> "Margot Louise Halvorsen"."""
        name = (self.name or "").strip()
        if "," in name:
            family, given = (part.strip() for part in name.split(",", 1))
            name = f"{given} {family}".strip()
        return _tidy_case(name)

    @property
    def display_name(self) -> str:
        """How MTC staff address a missionary: "Sister Halvorsen", "Elder Tanuvasa"."""
        title = (self.title_prefix or "").strip()
        if title.lower() == "couple":
            title = "Elder & Sister"
        if title and self.surname:
            return f"{title} {self.surname}"
        return self.full_name or self.name
