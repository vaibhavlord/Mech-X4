"""Request validation and the shared frontend response contract."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic import field_validator, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PullRequest(BaseModel):
    """A PR with an explicit timezone and a nonnegative account age."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(strict=True, ge=1)
    project: NonEmptyText
    author: NonEmptyText
    title: NonEmptyText
    body: str
    created_at: datetime
    account_age_days: int = Field(strict=True, ge=0)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value):
        """Reject ambiguous dates and numeric timestamps; compare in UTC."""
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("created_at must be an ISO 8601 datetime with a timezone")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("created_at must be a valid ISO 8601 datetime") from error
        if parsed.utcoffset() is None:
            raise ValueError("created_at must include Z or a timezone offset")
        try:
            return parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError) as error:
            raise ValueError("created_at is outside the supported UTC range") from error


class ScanRequest(BaseModel):
    """Require at least one PR and prevent duplicate IDs inflating a campaign."""

    model_config = ConfigDict(extra="forbid")
    pull_requests: list[PullRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self):
        """IDs identify distinct PRs within a scan."""
        ids = [pr.id for pr in self.pull_requests]
        if len(ids) != len(set(ids)):
            raise ValueError("pull_requests must have unique IDs")
        return self


class Project(BaseModel):
    id: str
    name: str


class Trust(BaseModel):
    from_project: str = Field(alias="from")
    to: str


class NetworkSignal(BaseModel):
    project_id: str
    contributor_id: str
    type: Literal["vouch", "denounce"]
    reason: str | None


class NetworkResponse(BaseModel):
    projects: list[Project]
    trusts: list[Trust]
    signals: list[NetworkSignal]


class EvaluatedSignal(BaseModel):
    project: str
    contributor: str
    reason: str | None


class EvaluationResponse(BaseModel):
    decision: Literal["vouched", "review", "unknown"]
    trusted_vouches: list[EvaluatedSignal]
    trusted_denounces: list[EvaluatedSignal]
    ignored_denounces: list[EvaluatedSignal]
    evidence: list[str]


class Campaign(BaseModel):
    id: str
    pr_ids: list[int]
    project_count: int
    similarity_score: float
    evidence: list[str]


class ScanResponse(BaseModel):
    total_prs: int
    campaigns: list[Campaign]


class ExplanationEvidence(BaseModel):
    """Measured campaign signals, without identities or unverified allegations."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pr_ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)
    project_count: int = Field(ge=1)
    similarity_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    account_ages: list[Annotated[int, Field(ge=0)]] = Field(
        min_length=1, description="Account age in days for each PR, in pr_ids order."
    )
    time_window_minutes: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_counts(self):
        """Require distinct PRs and consistent per-PR evidence."""
        if len(self.pr_ids) != len(set(self.pr_ids)):
            raise ValueError("pr_ids must be unique")
        if self.project_count > len(self.pr_ids):
            raise ValueError("project_count cannot exceed the number of PRs")
        if len(self.account_ages) != len(self.pr_ids):
            raise ValueError("account_ages must contain one observation per PR")
        return self


class ExplainRequest(BaseModel):
    """The only accepted input to the explanation endpoint."""

    model_config = ConfigDict(extra="forbid")
    evidence: ExplanationEvidence


class ModelAssessment(BaseModel):
    """The complete JSON object Ollama must generate; extra fields are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: NonEmptyText = Field(max_length=2000)
    risk_level: Literal["suspicious", "needs_review", "inconclusive"]
    evidence_referenced: list[str] = Field(min_length=1, max_length=5)


class ExplainResponse(ModelAssessment):
    """Server-owned model metadata is added only after successful validation."""

    summary: NonEmptyText = Field(max_length=2100)
    model: str
    used_model: Literal[True]
