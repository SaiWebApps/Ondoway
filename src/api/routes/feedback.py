"""Voice feedback route — transcribe → structure via LLM → create GitHub issue."""

from __future__ import annotations

import json
import os
import re

import anthropic
import httpx
from fastapi import APIRouter, HTTPException, Request

from src.api.models.feedback import FeedbackRequest, FeedbackResponse, TourContext

router = APIRouter(tags=["feedback"])

_GITHUB_REPO = "SaiWebApps/Ondoway"
_GITHUB_API = "https://api.github.com"

# RATE LIMITING REMOVED 2026-07-31 (owner order: all limiters). /feedback is
# unauthenticated and each call spends an Anthropic Haiku completion AND opens a
# real GitHub issue, so nothing now bounds either the spend or the volume of
# issues an anonymous caller can create. Deleted, not disabled.


_SYSTEM_PROMPT = (
    "You are a bug report structurer for a mobile app called Ondoway "
    "(GPS-triggered audio walking tours).\n\n"
    "You receive a raw voice transcript from a beta tester. "
    "Convert it into a structured GitHub issue.\n\n"
    "Return ONLY a JSON object with these fields:\n"
    '- "title": A concise issue title (under 80 chars). '
    "Start with [Bug], [Feature], or [UX].\n"
    '- "body": A markdown body with sections: '
    "**What happened**, **Expected behavior** (if inferable), "
    "**Steps to reproduce** (if inferable). Keep it concise.\n"
    '- "labels": An array of 1-3 labels from: '
    '"bug", "enhancement", "ux", "audio", "map", '
    '"auth", "tour", "performance", "crash".\n\n'
    "If the transcript is vague, do your best — "
    "the tester's words are the source of truth."
)


def _extract_json_object(text: str) -> str | None:
    """Best-effort pull of a single JSON object out of an LLM response.

    Handles (a) a fenced ```json ... ``` block (even without a closing fence)
    and (b) a bare object embedded in prose, via a brace-matching scan that is
    string/escape aware so braces inside JSON strings don't confuse it.
    Returns the object substring, or None if no plausible object is found.
    """
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _structure_feedback(transcript: str) -> dict:
    # BOUNDED, like every tour-engine client (src/tour/anthropic_client.py). A bare
    # anthropic.Anthropic() inherits the SDK's 600 s timeout x 2 retries — up to 30
    # minutes on ONE stalled call — and this runs inside a live request-blocking
    # route, so a stall holds a user's feedback submission open that whole time.
    # A Haiku call with max_tokens=500 that has not answered in 45 s is not going to.
    from src.tour.anthropic_client import judge_client

    client = judge_client()
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        )
    except anthropic.RateLimitError as exc:
        # Upstream throttling -> 503 with a retry hint, not a 500.
        raise HTTPException(
            status_code=503,
            detail=f"LLM provider rate limited: {exc}",
            headers={"Retry-After": "30"},
        ) from exc
    except anthropic.APIError as exc:
        # Any other provider failure (connection/status) -> clean 502.
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    # An empty content list or a non-text first block would raise
    # IndexError/AttributeError here — uncaught, that surfaces as a 500. Guard the
    # access so a missing/non-text response degrades to the deterministic fallback
    # below (empty text -> _extract_json_object returns None).
    block = message.content[0] if message.content else None
    text = getattr(block, "text", "").strip()
    candidate = _extract_json_object(text)
    if candidate is not None:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    # The LLM returned prose / malformed JSON. Never lose the report —
    # degrade to a deterministic issue built straight from the transcript.
    return {
        "title": f"[Feedback] {transcript[:60]}",
        "body": transcript,
        "labels": ["beta-feedback"],
    }


def _tour_context_section(ctx: TourContext) -> str:
    """Render the workbench tour-eval context as a '## Tour Context' markdown section.

    Human-mediated loop: the context is rendered for a human
    reader on the GitHub issue — nothing here feeds back into the engine.
    """
    verdict_label = "👍 up" if ctx.verdict == "up" else "👎 down"
    end_label = f"{ctx.end[0]:.5f}, {ctx.end[1]:.5f}" if ctx.end else "open walk"
    lines = [
        "## Tour Context",
        f"**Verdict:** {verdict_label}",
        f"**Start:** {ctx.start[0]:.5f}, {ctx.start[1]:.5f}",
        f"**End:** {end_label}",
        f"**Duration:** {ctx.duration_min} min",
        f"**Lenses:** {', '.join(ctx.lenses) if ctx.lenses else '—'}",
    ]
    if ctx.stops:
        lines.append("**Stops:**")
        lines.extend(f"{i}. {s.name} ({s.band})" for i, s in enumerate(ctx.stops, 1))
    if ctx.note:
        lines.append(f"**Note:** {ctx.note}")
    return "\n".join(lines)


def _create_github_issue(title: str, body: str, labels: list[str]) -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(500, "GITHUB_TOKEN not configured")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{_GITHUB_API}/repos/{_GITHUB_REPO}/issues",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"title": title, "body": body, "labels": labels},
            )
    except httpx.TimeoutException as exc:
        # A transport timeout must degrade to a clean 503 with a
        # retry hint, not a raw 500 stack trace that loses the tester's report.
        raise HTTPException(
            status_code=503,
            detail="GitHub API timeout",
            headers={"Retry-After": "30"},
        ) from exc
    except httpx.RequestError as exc:
        # Connection/network errors (ConnectError, etc.) -> clean 502.
        raise HTTPException(status_code=502, detail=f"GitHub API unreachable: {exc}") from exc

    if resp.status_code != 201:
        raise HTTPException(502, f"GitHub API error: {resp.status_code} {resp.text}")

    data = resp.json()
    return {"issue_url": data["html_url"], "issue_number": data["number"], "title": title}


@router.post("/feedback", response_model=FeedbackResponse, status_code=201)
def submit_feedback(request: Request, body: FeedbackRequest):
    """Accept raw voice transcript, structure it via LLM, and create a GitHub issue."""
    metadata_lines = []
    if body.user_email:
        metadata_lines.append(f"**Reporter:** {body.user_email}")
    if body.current_route:
        metadata_lines.append(f"**Screen:** `{body.current_route}`")
    if body.device_platform:
        metadata_lines.append(
            f"**Device:** {body.device_platform} {body.device_os_version}"
        )
    if body.app_version:
        metadata_lines.append(f"**App version:** {body.app_version}")

    metadata_section = "\n".join(metadata_lines)

    structured = _structure_feedback(body.transcript)
    # The LLM output is untrusted. Coerce the shape defensively so a
    # non-dict top level or a non-list/non-str 'labels' can never crash the route
    # (or forward junk labels to the GitHub API and trigger a 422 -> 502).
    if not isinstance(structured, dict):
        structured = {}

    issue_body = structured.get("body", body.transcript)
    if not isinstance(issue_body, str):
        issue_body = body.transcript
    if metadata_section:
        issue_body = f"{issue_body}\n\n---\n{metadata_section}"

    # Append the tour eval context when present (absent -> unchanged).
    if body.tour_context is not None:
        issue_body = f"{issue_body}\n\n{_tour_context_section(body.tour_context)}"

    raw_labels = structured.get("labels", [])
    if not isinstance(raw_labels, list):
        raw_labels = []
    labels = [x for x in raw_labels if isinstance(x, str)]
    if "beta-feedback" not in labels:
        labels.append("beta-feedback")

    title = structured.get("title")
    if not isinstance(title, str) or not title:
        title = f"[Feedback] {body.transcript[:60]}"

    result = _create_github_issue(
        title=title,
        body=issue_body,
        labels=labels,
    )
    return FeedbackResponse(**result)
