"""Structured validation shared by readers and analysis objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    """Severity of a validation issue."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A data problem with enough context to locate its source."""

    code: str
    message: str
    severity: Severity
    source: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        location = self.source or "<unknown source>"
        if self.line is not None:
            location = f"{location}:{self.line}"
        return f"{location}: {self.severity.value}: {self.code}: {self.message}"


class ValidationError(ValueError):
    """Raised when strict validation encounters invalid input."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        summary = "\n".join(str(issue) for issue in issues)
        super().__init__(summary)


@dataclass(slots=True)
class ValidationReport:
    """Mutable collector returned with validated artifacts and results."""

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        code: str,
        message: str,
        severity: Severity,
        *,
        source: str | None = None,
        line: int | None = None,
    ) -> ValidationIssue:
        issue = ValidationIssue(code, message, severity, source, line)
        self.issues.append(issue)
        return issue

    def error(
        self,
        code: str,
        message: str,
        *,
        source: str | None = None,
        line: int | None = None,
    ) -> ValidationIssue:
        return self.add(code, message, Severity.ERROR, source=source, line=line)

    def warning(
        self,
        code: str,
        message: str,
        *,
        source: str | None = None,
        line: int | None = None,
    ) -> ValidationIssue:
        return self.add(code, message, Severity.WARNING, source=source, line=line)

    def info(
        self,
        code: str,
        message: str,
        *,
        source: str | None = None,
        line: int | None = None,
    ) -> ValidationIssue:
        return self.add(code, message, Severity.INFO, source=source, line=line)

    def extend(self, other: ValidationReport) -> None:
        self.issues.extend(other.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is Severity.WARNING
        )

    @property
    def infos(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.INFO)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if errors := self.errors:
            raise ValidationError(errors)

    def __bool__(self) -> bool:
        return bool(self.issues)
