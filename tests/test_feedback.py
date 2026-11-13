"""Tests for the voice feedback → GitHub Issues endpoint."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    with patch("src.api.app.init_driver"), patch("src.api.app.close_driver"):
        from src.api.app import create_app

        app = create_app()
        with TestClient(app) as c:
            yield c


class TestFeedbackEndpoint:
    def test_missing_transcript_returns_422(self, client):
        resp = client.post("/api/v1/feedback", json={})
        assert resp.status_code == 422

    def test_empty_transcript_returns_422(self, client):
        resp = client.post("/api/v1/feedback", json={"transcript": ""})
        assert resp.status_code == 422

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_success_flow(self, mock_structure, mock_github, client):
        mock_structure.return_value = {
            "title": "[Bug] Map doesn't load",
            "body": "**What happened**\nThe map screen is blank.",
            "labels": ["bug", "map"],
        }
        mock_github.return_value = {
            "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/99",
            "issue_number": 99,
            "title": "[Bug] Map doesn't load",
        }

        resp = client.post(
            "/api/v1/feedback",
            json={
                "transcript": "the map doesn't load when I open the explore tab",
                "current_route": "/explore",
                "device_platform": "iOS",
                "device_os_version": "18.4",
                "app_version": "0.1.0",
                "user_email": "tester@example.com",
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["issue_url"] == "https://github.com/SaiWebApps/Ondoway/issues/99"
        assert data["issue_number"] == 99
        assert data["title"] == "[Bug] Map doesn't load"

        mock_structure.assert_called_once()
        call_kwargs = mock_github.call_args.kwargs
        assert "beta-feedback" in call_kwargs["labels"]

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_metadata_appended_to_body(self, mock_structure, mock_github, client):
        mock_structure.return_value = {
            "title": "[UX] Colors are weird",
            "body": "**What happened**\nColors look off in dark mode.",
            "labels": ["ux"],
        }
        mock_github.return_value = {
            "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/100",
            "issue_number": 100,
            "title": "[UX] Colors are weird",
        }

        client.post(
            "/api/v1/feedback",
            json={
                "transcript": "the colors are weird in dark mode",
                "current_route": "/profile",
                "device_platform": "Android",
                "user_email": "beta@test.com",
            },
        )

        call_kwargs = mock_github.call_args.kwargs
        issue_body = call_kwargs["body"]
        assert "beta@test.com" in issue_body
        assert "/profile" in issue_body
        assert "Android" in issue_body

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_beta_feedback_label_always_added(
        self, mock_structure, mock_github, client
    ):
        mock_structure.return_value = {
            "title": "[Feature] Offline mode",
            "body": "User wants offline tours.",
            "labels": ["enhancement"],
        }
        mock_github.return_value = {
            "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/101",
            "issue_number": 101,
            "title": "[Feature] Offline mode",
        }

        client.post(
            "/api/v1/feedback",
            json={"transcript": "I wish I could download tours for offline use"},
        )

        call_kwargs = mock_github.call_args.kwargs
        labels = call_kwargs["labels"]
        assert "beta-feedback" in labels
        assert "enhancement" in labels

    @patch("src.api.routes.feedback._structure_feedback")
    def test_github_token_missing_returns_500(self, mock_structure, client, monkeypatch):
        mock_structure.return_value = {
            "title": "[Bug] Test",
            "body": "Test body",
            "labels": ["bug"],
        }
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        resp = client.post(
            "/api/v1/feedback",
            json={"transcript": "something is broken"},
        )
        assert resp.status_code == 500

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_no_auth_required(self, mock_structure, mock_github, client):
        """The feedback endpoint must work without an Authorization header."""
        mock_structure.return_value = {
            "title": "[Bug] Login broken",
            "body": "Can't log in.",
            "labels": ["bug", "auth"],
        }
        mock_github.return_value = {
            "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/102",
            "issue_number": 102,
            "title": "[Bug] Login broken",
        }

        resp = client.post(
            "/api/v1/feedback",
            json={"transcript": "I can't log in the app just spins"},
        )
        assert resp.status_code == 201


class TestTourContext:
    """The optional tour_context renders a '## Tour Context' section
    into the GitHub issue body (human-mediated eval loop — never auto-tuning)."""

    _GITHUB_RESULT: ClassVar[dict] = {
        "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/321",
        "issue_number": 321,
        "title": "[UX] Tour feedback",
    }
    _STRUCTURED: ClassVar[dict] = {
        "title": "[UX] Tour feedback",
        "body": "**What happened**\nToo much walking between stops.",
        "labels": ["ux", "tour"],
    }

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_tour_context_rendered_into_issue_body(self, mock_structure, mock_github, client):
        mock_structure.return_value = self._STRUCTURED
        mock_github.return_value = self._GITHUB_RESULT

        resp = client.post(
            "/api/v1/feedback",
            json={
                "transcript": "Too much walking between stops.",
                "tour_context": {
                    "start": [48.8566, 2.3522],
                    "end": None,
                    "duration_min": 60,
                    "lenses": ["dark_history"],
                    "stops": [
                        {"name": "Notre-Dame", "band": "dwell"},
                        {"name": "Fontaine du Palmier", "band": "vignette"},
                    ],
                    "verdict": "down",
                    "note": "Too much walking between stops.",
                },
            },
        )
        assert resp.status_code == 201

        issue_body = mock_github.call_args.kwargs["body"]
        # The structured body is preserved; the Tour Context section is appended.
        assert issue_body.startswith("**What happened**")
        assert "## Tour Context" in issue_body
        assert "**Verdict:** 👎 down" in issue_body
        assert "**Start:** 48.85660, 2.35220" in issue_body
        assert "**End:** open walk" in issue_body
        assert "**Duration:** 60 min" in issue_body
        assert "**Lenses:** dark_history" in issue_body
        assert "1. Notre-Dame (dwell)" in issue_body
        assert "2. Fontaine du Palmier (vignette)" in issue_body
        assert "**Note:** Too much walking between stops." in issue_body

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_tour_context_end_verdict_up_and_empty_lenses(
        self, mock_structure, mock_github, client
    ):
        mock_structure.return_value = self._STRUCTURED
        mock_github.return_value = self._GITHUB_RESULT

        resp = client.post(
            "/api/v1/feedback",
            json={
                "transcript": "Tour feedback (up) from workbench",
                "tour_context": {
                    "start": [48.8566, 2.3522],
                    "end": [48.8606, 2.3376],
                    "duration_min": 90,
                    "lenses": [],
                    "stops": [{"name": "Notre-Dame", "band": "dwell"}],
                    "verdict": "up",
                    "note": None,
                },
            },
        )
        assert resp.status_code == 201

        issue_body = mock_github.call_args.kwargs["body"]
        assert "**Verdict:** 👍 up" in issue_body
        assert "**End:** 48.86060, 2.33760" in issue_body
        assert "**Lenses:** —" in issue_body
        assert "**Note:**" not in issue_body

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_absent_tour_context_leaves_body_unchanged(
        self, mock_structure, mock_github, client
    ):
        """No tour_context -> the issue body is EXACTLY the structured body (no metadata
        fields sent either) — proves the appended section is strictly opt-in."""
        mock_structure.return_value = self._STRUCTURED
        mock_github.return_value = self._GITHUB_RESULT

        resp = client.post(
            "/api/v1/feedback",
            json={"transcript": "plain feedback, no tour attached"},
        )
        assert resp.status_code == 201

        issue_body = mock_github.call_args.kwargs["body"]
        assert issue_body == self._STRUCTURED["body"]
        assert "Tour Context" not in issue_body

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_bad_verdict_rejected_422(self, mock_structure, mock_github, client):
        """Validation rejects a verdict outside 'up'/'down' before any LLM/GitHub call."""
        resp = client.post(
            "/api/v1/feedback",
            json={
                "transcript": "x",
                "tour_context": {
                    "start": [48.8566, 2.3522],
                    "duration_min": 60,
                    "verdict": "sideways",
                },
            },
        )
        assert resp.status_code == 422
        mock_structure.assert_not_called()
        mock_github.assert_not_called()

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_bad_start_rejected_422(self, mock_structure, mock_github, client):
        """start must be exactly [lat, lng]."""
        resp = client.post(
            "/api/v1/feedback",
            json={
                "transcript": "x",
                "tour_context": {
                    "start": [48.8566, 2.3522, 12.0],
                    "duration_min": 60,
                    "verdict": "up",
                },
            },
        )
        assert resp.status_code == 422
        mock_structure.assert_not_called()
        mock_github.assert_not_called()


class TestRateLimit:
    """Defect #1: the unauthenticated endpoint must not let one caller loop it
    to burn Anthropic credits / flood the issue tracker."""


  # global cap trips despite every IP being distinct


class TestMalformedLLMOutput:
    """Defects #2 and #4: untrusted LLM output must never surface as a 500."""

    @patch("src.api.routes.feedback._create_github_issue")
    def test_prose_wrapped_response_still_creates_issue(self, mock_github, client):
        """Defect #2: the LLM returns prose around an unclosed fence -> the report
        must still land as a 201 issue (deterministic fallback), not a 500."""
        mock_github.return_value = {
            "issue_url": "https://github.com/SaiWebApps/Ondoway/issues/3",
            "issue_number": 3,
            "title": "[Feedback] audio cuts out",
        }

        # A response with an OPENING fence but no closing fence, wrapped in prose.
        bad_text = "Sure, here you go:\n```json\n{ not valid json at all"
        with patch("src.tour.anthropic_client.judge_client") as mock_judge_client:
            mock_block = mock_judge_client.return_value.messages.create.return_value.content[0]
            mock_block.text = bad_text

            resp = client.post(
                "/api/v1/feedback",
                json={"transcript": "audio cuts out mid tour"},
            )

        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 201
        # Fallback used the transcript, not a crash.
        assert mock_github.call_args.kwargs["body"] == "audio cuts out mid tour"

    @patch("src.api.routes.feedback._create_github_issue")
    def test_embedded_object_in_prose_is_extracted(self, mock_github, client):
        """The brace-matching scan pulls a bare JSON object out of prose (no fence)."""
        mock_github.return_value = {
            "issue_url": "u",
            "issue_number": 4,
            "title": "[Bug] parsed",
        }
        good = 'Here: {"title": "[Bug] parsed", "body": "it works", "labels": ["bug"]} done'
        with patch("src.tour.anthropic_client.judge_client") as mock_judge_client:
            mock_block = mock_judge_client.return_value.messages.create.return_value.content[0]
            mock_block.text = good
            resp = client.post("/api/v1/feedback", json={"transcript": "x"})

        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 201
        assert mock_github.call_args.kwargs["title"] == "[Bug] parsed"
        assert mock_github.call_args.kwargs["body"] == "it works"

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_labels_non_list_does_not_500(self, mock_structure, mock_github, client):
        """Defect #4: labels returned as a bare string must not AttributeError -> 500."""
        mock_structure.return_value = {"title": "t", "body": "b", "labels": "bug"}
        mock_github.return_value = {"issue_url": "u", "issue_number": 5, "title": "t"}

        resp = client.post("/api/v1/feedback", json={"transcript": "x"})
        assert resp.status_code == 201
        # The bare string is dropped; only beta-feedback remains.
        assert mock_github.call_args.kwargs["labels"] == ["beta-feedback"]

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_labels_list_of_non_strings_are_filtered(
        self, mock_structure, mock_github, client
    ):
        """Defect #4: non-str label items are filtered so they never reach GitHub."""
        mock_structure.return_value = {
            "title": "t",
            "body": "b",
            "labels": ["bug", 123, {"x": 1}, "ux"],
        }
        mock_github.return_value = {"issue_url": "u", "issue_number": 6, "title": "t"}

        resp = client.post("/api/v1/feedback", json={"transcript": "x"})
        assert resp.status_code == 201
        labels = mock_github.call_args.kwargs["labels"]
        assert labels == ["bug", "ux", "beta-feedback"]

    @patch("src.api.routes.feedback._create_github_issue")
    def test_empty_content_list_falls_back_to_transcript(self, mock_github, client):
        """Defect #1: an empty LLM content list (content=[]) must not IndexError -> 500.
        It should degrade to the deterministic transcript fallback (201)."""
        mock_github.return_value = {
            "issue_url": "u",
            "issue_number": 8,
            "title": "[Feedback] empty content",
        }
        with patch("src.tour.anthropic_client.judge_client") as mock_judge_client:
            mock_judge_client.return_value.messages.create.return_value.content = []

            resp = client.post(
                "/api/v1/feedback",
                json={"transcript": "empty content body"},
            )

        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 201
        assert mock_github.call_args.kwargs["body"] == "empty content body"

    @patch("src.api.routes.feedback._create_github_issue")
    def test_non_text_first_block_falls_back_to_transcript(self, mock_github, client):
        """Defect #1: a first content block with no `.text` attribute (e.g. a
        tool_use block) must not AttributeError -> 500; it degrades to the
        deterministic transcript fallback (201)."""
        mock_github.return_value = {
            "issue_url": "u",
            "issue_number": 9,
            "title": "[Feedback] non-text block",
        }

        class _NonTextBlock:
            """A block with no `text` attribute, like a tool_use content block."""

        with patch("src.tour.anthropic_client.judge_client") as mock_judge_client:
            mock_judge_client.return_value.messages.create.return_value.content = [
                _NonTextBlock()
            ]

            resp = client.post(
                "/api/v1/feedback",
                json={"transcript": "non text block body"},
            )

        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 201
        assert mock_github.call_args.kwargs["body"] == "non text block body"

    @patch("src.api.routes.feedback._create_github_issue")
    @patch("src.api.routes.feedback._structure_feedback")
    def test_top_level_non_dict_does_not_500(self, mock_structure, mock_github, client):
        """Defect #4: a top-level JSON list (not an object) must not crash the route."""
        mock_structure.return_value = ["not", "a", "dict"]
        mock_github.return_value = {"issue_url": "u", "issue_number": 7, "title": "t"}

        resp = client.post("/api/v1/feedback", json={"transcript": "salvage me"})
        assert resp.status_code == 201
        kwargs = mock_github.call_args.kwargs
        assert kwargs["labels"] == ["beta-feedback"]
        assert kwargs["body"] == "salvage me"  # falls back to transcript
        assert kwargs["title"] == "[Feedback] salvage me"


class TestProviderErrors:
    """Defect #3: upstream Anthropic failures must return 502/503, not 500."""

    @patch("src.tour.anthropic_client.judge_client")
    def test_connection_error_returns_502(self, mock_judge_client, client):
        import httpx as _httpx

        mock_judge_client.return_value.messages.create.side_effect = (
            anthropic.APIConnectionError(request=_httpx.Request("POST", "https://x"))
        )
        resp = client.post("/api/v1/feedback", json={"transcript": "provider down"})
        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 502

    @patch("src.tour.anthropic_client.judge_client")
    def test_rate_limit_error_returns_503_with_retry_after(self, mock_judge_client, client):
        import httpx as _httpx

        response = _httpx.Response(429, request=_httpx.Request("POST", "https://x"))
        mock_judge_client.return_value.messages.create.side_effect = anthropic.RateLimitError(
            "rate limited", response=response, body=None
        )
        resp = client.post("/api/v1/feedback", json={"transcript": "throttled"})
        mock_judge_client.assert_called_once_with()
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "30"


class TestGitHubTransportErrors:
    """Defect #2: httpx transport errors from client.post() must return 502/503
    with a Retry-After hint, not an opaque 500 stack trace that loses the report."""

    @staticmethod
    def _client_raising(exc: Exception):
        """Build a mock replacement for feedback.httpx.Client whose context-managed
        .post() raises `exc`. Patching the module symbol (not httpx.Client.post
        globally) keeps the TestClient's own httpx machinery intact."""
        from unittest.mock import MagicMock

        instance = MagicMock()
        instance.post.side_effect = exc
        factory = MagicMock()
        factory.return_value.__enter__.return_value = instance
        return factory

    @patch("src.api.routes.feedback._structure_feedback")
    def test_github_timeout_returns_503_with_retry_after(
        self, mock_structure, client, monkeypatch
    ):
        mock_structure.return_value = {"title": "[Bug] t", "body": "b", "labels": ["bug"]}
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")

        with patch(
            "src.api.routes.feedback.httpx.Client",
            self._client_raising(httpx.TimeoutException("timed out")),
        ):
            resp = client.post("/api/v1/feedback", json={"transcript": "gh down"})

        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "30"

    @patch("src.api.routes.feedback._structure_feedback")
    def test_github_connect_error_returns_502(self, mock_structure, client, monkeypatch):
        mock_structure.return_value = {"title": "[Bug] t", "body": "b", "labels": ["bug"]}
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")

        with patch(
            "src.api.routes.feedback.httpx.Client",
            self._client_raising(httpx.ConnectError("connection refused")),
        ):
            resp = client.post("/api/v1/feedback", json={"transcript": "gh unreachable"})

        assert resp.status_code == 502
