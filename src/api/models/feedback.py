"""Pydantic models for the voice feedback → GitHub Issues endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TourContextStop(BaseModel):
    """One rendered stop of the tour being judged (name + spotlight band)."""

    name: str = Field(..., max_length=300)
    band: str = Field(..., max_length=40)


class TourContext(BaseModel):
    """Structured context of a generated tour attached to workbench eval feedback.

    Human-mediated loop: this context only lands in the GitHub
    issue for a human to read — it never tunes the engine automatically.
    """

    start: tuple[float, float]
    end: tuple[float, float] | None = None
    duration_min: int
    lenses: list[str] = Field(default_factory=list)
    stops: list[TourContextStop] = Field(default_factory=list)
    verdict: Literal["up", "down"]
    note: str | None = Field(default=None, max_length=2000)


class FeedbackRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=5000)
    current_route: str = Field(default="", max_length=200)
    device_platform: str = Field(default="", max_length=50)
    device_os_version: str = Field(default="", max_length=50)
    app_version: str = Field(default="", max_length=50)
    user_email: str | None = Field(default=None, max_length=200)
    tour_context: TourContext | None = None


class FeedbackResponse(BaseModel):
    issue_url: str
    issue_number: int
    title: str
