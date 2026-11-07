"""Plain data objects passed between the extraction, rules and interface layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Outcome for a single field, and for a label overall."""

    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"


class BeverageClass(str, Enum):
    """Commodity, which decides the alcohol content tolerance."""

    SPIRITS = "spirits"
    WINE = "wine"
    MALT = "malt"
    UNKNOWN = "unknown"


@dataclass
class ApplicationRecord:
    """What the applicant declared on the application form."""

    label_id: str
    brand_name: str
    class_type: str
    alcohol_content: str
    net_contents: str
    image_file: str = ""


@dataclass
class ExtractedLabel:
    """What the vision model read off the label artwork. No judgements here."""

    brand_name: str | None = None
    class_type: str | None = None
    alcohol_content: str | None = None
    net_contents: str | None = None
    government_warning: str | None = None
    warning_header_bold: bool | None = None
    legibility_notes: str = ""


@dataclass
class FieldResult:
    """The verdict on one field, with enough detail for a reviewer to act on."""

    field: str
    status: Status
    expected: str
    found: str
    message: str


@dataclass
class VerificationResult:
    """Everything known about one label after it has been checked."""

    label_id: str
    fields: list[FieldResult] = field(default_factory=list)
    extracted: ExtractedLabel | None = None
    error: str | None = None
    seconds: float = 0.0

    @property
    def overall(self) -> Status:
        """FAIL if any field failed, REVIEW if any needs a look, else PASS."""
        if self.error:
            return Status.REVIEW
        statuses = {f.status for f in self.fields}
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.REVIEW in statuses or not self.fields:
            return Status.REVIEW
        return Status.PASS
