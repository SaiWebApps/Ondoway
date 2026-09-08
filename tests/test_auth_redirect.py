"""Tests for the magic link redirect flow — no Neo4j needed."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.auth.email import _build_magic_link


class TestBuildMagicLinkUsesConfig:
    @patch("src.api.auth.email.FRONTEND_URL", "http://localhost:8000")
    def test_dev_url(self):
        link = _build_magic_link("abc123")
        assert link == "http://localhost:8000/auth?token=abc123"

    @patch("src.api.auth.email.FRONTEND_URL", "https://ondoway.com")
    def test_prod_url(self):
        link = _build_magic_link("abc123")
        assert link == "https://ondoway.com/auth?token=abc123"


class TestAuthRedirectRoute:
    def _make_client(self):
        from src.api.app import create_app

        app = create_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_auth_route_returns_html(self):
        client = self._make_client()
        resp = client.get("/auth?token=test-tok")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_auth_html_contains_deep_link_script(self):
        client = self._make_client()
        resp = client.get("/auth?token=test-tok")
        body = resp.text
        assert "ondoway://auth/callback" in body

    def test_auth_html_reads_token_from_url(self):
        client = self._make_client()
        resp = client.get("/auth?token=test-tok")
        body = resp.text
        assert "params.get('token')" in body

    def test_auth_route_without_token_still_serves_page(self):
        client = self._make_client()
        resp = client.get("/auth")
        assert resp.status_code == 200


class TestJoinFamilyRedirectRoute:
    """The invite URL (FRONTEND_URL/auth/join-family?token=…) must land
    somewhere real for a browser or a phone without the app — the auth.html
    mould: deep-link into the app with the token, plus a get-the-app line."""

    def _make_client(self):
        from src.api.app import create_app

        app = create_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_join_family_route_returns_html(self):
        client = self._make_client()
        resp = client.get("/auth/join-family?token=test-tok")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_join_family_html_deep_links_into_the_app_join_route(self):
        client = self._make_client()
        resp = client.get("/auth/join-family?token=test-tok")
        assert "ondoway://auth/join-family" in resp.text

    def test_join_family_html_reads_token_from_url(self):
        client = self._make_client()
        resp = client.get("/auth/join-family?token=test-tok")
        assert "params.get('token')" in resp.text

    def test_join_family_html_carries_a_get_the_app_line(self):
        client = self._make_client()
        resp = client.get("/auth/join-family?token=test-tok")
        assert "installed" in resp.text

    def test_join_family_route_without_token_still_serves_page(self):
        client = self._make_client()
        resp = client.get("/auth/join-family")
        assert resp.status_code == 200
