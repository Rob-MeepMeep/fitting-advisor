"""Skill presets and EVE Online ESI SSO (OAuth 2.0 PKCE) skill import.

Tokens are stored ONLY in the user's OS local app data directory via
``platformdirs`` (never inside the repository) and are never logged.
Skills are mapped strictly by EVE type ID (never by name).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from platformdirs import user_data_dir
from pydantic import BaseModel

from advisor.models import SkillSet

EVE_SSO_AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
ESI_BASE_URL = "https://esi.evetech.net"
ESI_SKILLS_SCOPE = "esi-skills.read_skills.v1"
DEFAULT_COMPATIBILITY_DATE = "2025-12-16"
DEFAULT_USER_AGENT = "fitting-advisor/0.1 (local EVE fitting tool; contact via repo)"


def all0_skillset() -> SkillSet:
    return SkillSet(preset="all0", character_name="All 0")


def all5_skillset() -> SkillSet:
    return SkillSet(preset="all5", character_name="All 5")


def get_preset(name: str) -> SkillSet:
    norm = name.strip().lower()
    if norm in ("all0", "all_0", "all 0"):
        return all0_skillset()
    if norm in ("all5", "all_5", "all 5", "allv", "all_v"):
        return all5_skillset()
    raise ValueError(f"Unknown skill preset: {name!r}. Use 'all0' or 'all5'.")


def default_token_dir() -> Path:
    """Return OS-appropriate local app data directory for storing ESI tokens."""
    return Path(user_data_dir("fitting-advisor", appauthor=False))


def generate_pkce_pair() -> tuple[str, str]:
    """Generate an OAuth 2.0 PKCE (code_verifier, S256 code_challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str = ESI_SKILLS_SCOPE,
) -> str:
    """Build the EVE SSO v2 OAuth 2.0 PKCE authorization URL."""
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{EVE_SSO_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def extract_character_from_jwt(access_token: str) -> tuple[int, str]:
    """Decode character_id and character_name from an EVE SSO JWT payload."""
    parts = access_token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid JWT access token format")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    payload = json.loads(payload_bytes.decode("utf-8"))
    sub = str(payload.get("sub") or "")
    # Expected format: "CHARACTER:EVE:<character_id>"
    sub_parts = sub.split(":")
    if len(sub_parts) < 3 or not sub_parts[2].isdigit():
        raise ValueError("JWT 'sub' claim does not contain a valid EVE character ID")
    char_id = int(sub_parts[2])
    char_name = str(payload.get("name") or f"Character {char_id}")
    return char_id, char_name


class StoredToken(BaseModel):
    character_id: int
    character_name: str
    access_token: str
    refresh_token: str
    expires_at: float
    skills: dict[int, int] = {}
    skills_etag: str | None = None
    skills_expires_at: float = 0.0


class TokenStore:
    """Stores ESI OAuth tokens and cached skills in local app data (never in repo)."""

    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = (storage_dir or default_token_dir()).resolve()
        self.token_file = self.storage_dir / "esi_tokens.json"

    def load_all(self) -> dict[int, StoredToken]:
        if not self.token_file.is_file():
            return {}
        try:
            raw = json.loads(self.token_file.read_text(encoding="utf-8"))
            return {int(k): StoredToken(**v) for k, v in raw.items()}
        except (OSError, ValueError):
            return {}

    def get(self, character_id: int) -> StoredToken | None:
        return self.load_all().get(int(character_id))

    def save(self, token: StoredToken) -> None:
        all_tokens = self.load_all()
        all_tokens[token.character_id] = token
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {str(k): v.model_dump() for k, v in all_tokens.items()}
        self.token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def delete(self, character_id: int) -> None:
        all_tokens = self.load_all()
        if int(character_id) in all_tokens:
            del all_tokens[int(character_id)]
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            payload = {str(k): v.model_dump() for k, v in all_tokens.items()}
            self.token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_characters(self) -> list[dict[str, Any]]:
        """Return safe public metadata (no tokens) for stored characters."""
        return [
            {
                "character_id": t.character_id,
                "character_name": t.character_name,
                "skill_count": len(t.skills),
            }
            for t in self.load_all().values()
        ]


class ESIErrorLimitExceeded(RuntimeError):
    """Raised when ESI error-limit remaining budget is too low."""


class ESIClient:
    """HTTP client for EVE SSO PKCE and ESI character skills."""

    def __init__(
        self,
        client_id: str | None = None,
        user_agent: str | None = None,
        compatibility_date: str | None = None,
        token_store: TokenStore | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.client_id = client_id if client_id is not None else os.environ.get("ESI_CLIENT_ID", "")
        self.user_agent = user_agent or os.environ.get("ESI_USER_AGENT") or DEFAULT_USER_AGENT
        self.compatibility_date = (
            compatibility_date
            or os.environ.get("ESI_COMPATIBILITY_DATE")
            or DEFAULT_COMPATIBILITY_DATE
        )
        self.token_store = token_store or TokenStore()
        self._http_client = http_client
        self._error_limit_remain: int = 100
        self._error_limit_reset_at: float = 0.0

    def _client(self) -> httpx.Client:
        if self._http_client is not None:
            return self._http_client
        return httpx.Client(timeout=15.0)

    def _esi_headers(self, access_token: str, etag: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "X-Compatibility-Date": self.compatibility_date,
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _update_error_limit(self, headers: httpx.Headers) -> None:
        remain = headers.get("X-ESI-Error-Limit-Remain")
        reset = headers.get("X-ESI-Error-Limit-Reset")
        if remain is not None and remain.isdigit():
            self._error_limit_remain = int(remain)
        if reset is not None and reset.isdigit():
            self._error_limit_reset_at = time.time() + int(reset)

    def _check_error_limit(self) -> None:
        if self._error_limit_remain <= 5 and time.time() < self._error_limit_reset_at:
            wait_s = max(1, int(self._error_limit_reset_at - time.time()))
            raise ESIErrorLimitExceeded(
                f"ESI error limit nearly exhausted ({self._error_limit_remain} remaining). "
                f"Backing off for {wait_s}s."
            )

    def exchange_code(self, code: str, code_verifier: str) -> StoredToken:
        """Exchange an authorization code + PKCE verifier for EVE SSO tokens."""
        if not self.client_id:
            raise ValueError("ESI_CLIENT_ID is not configured")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.user_agent,
        }
        owns_client = self._http_client is None
        client = self._client()
        try:
            resp = client.post(EVE_SSO_TOKEN_URL, data=data, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if owns_client:
                client.close()

        access_token = str(payload["access_token"])
        refresh_token = str(payload["refresh_token"])
        expires_in = float(payload.get("expires_in", 1199))
        char_id, char_name = extract_character_from_jwt(access_token)

        token = StoredToken(
            character_id=char_id,
            character_name=char_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in - 30.0,
        )
        self.token_store.save(token)
        return token

    def refresh_access_token(self, token: StoredToken) -> StoredToken:
        """Refresh an expired EVE SSO access token."""
        if not self.client_id:
            raise ValueError("ESI_CLIENT_ID is not configured")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": self.client_id,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.user_agent,
        }
        owns_client = self._http_client is None
        client = self._client()
        try:
            resp = client.post(EVE_SSO_TOKEN_URL, data=data, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if owns_client:
                client.close()

        token.access_token = str(payload["access_token"])
        if payload.get("refresh_token"):
            token.refresh_token = str(payload["refresh_token"])
        token.expires_at = time.time() + float(payload.get("expires_in", 1199)) - 30.0
        self.token_store.save(token)
        return token

    def fetch_skills(self, character_id: int) -> SkillSet:
        """Fetch character skills from ESI, respecting Expires/ETag cache & error limits."""
        token = self.token_store.get(character_id)
        if token is None:
            raise KeyError(f"No stored ESI token for character {character_id}")

        now = time.time()
        # Respect ESI Expires cache header
        if token.skills and now < token.skills_expires_at:
            return SkillSet(
                preset="custom",
                character_name=token.character_name,
                skills=dict(token.skills),
            )

        self._check_error_limit()

        if now >= token.expires_at:
            token = self.refresh_access_token(token)

        url = f"{ESI_BASE_URL}/characters/{token.character_id}/skills/"
        headers = self._esi_headers(token.access_token, etag=token.skills_etag)

        owns_client = self._http_client is None
        client = self._client()
        try:
            resp = client.get(url, headers=headers)
        finally:
            if owns_client:
                client.close()

        self._update_error_limit(resp.headers)

        if resp.status_code == 304 and token.skills:
            # Not modified; update expires_at if provided
            token.skills_expires_at = self._parse_expires(resp.headers, default_ttl=300.0)
            self.token_store.save(token)
            return SkillSet(
                preset="custom",
                character_name=token.character_name,
                skills=dict(token.skills),
            )

        resp.raise_for_status()
        data = resp.json()

        # Map skills strictly by type ID (skill_id -> active_skill_level)
        skill_map: dict[int, int] = {}
        for row in data.get("skills", []):
            sid = int(row["skill_id"])
            lvl = int(row.get("active_skill_level", row.get("trained_skill_level", 0)))
            skill_map[sid] = max(0, min(5, lvl))

        token.skills = skill_map
        token.skills_etag = resp.headers.get("ETag")
        token.skills_expires_at = self._parse_expires(resp.headers, default_ttl=300.0)
        self.token_store.save(token)

        return SkillSet(
            preset="custom",
            character_name=token.character_name,
            skills=skill_map,
        )

    @staticmethod
    def _parse_expires(headers: httpx.Headers, default_ttl: float = 300.0) -> float:
        expires_hdr = headers.get("Expires")
        if expires_hdr:
            try:
                return parsedate_to_datetime(expires_hdr).timestamp()
            except (TypeError, ValueError):
                pass
        return time.time() + default_ttl


def run_pkce_callback_server(
    expected_state: str,
    port: int = 8765,
    timeout: float = 180.0,
) -> str:
    """Listen once on 127.0.0.1:<port>/callback for the EVE SSO authorization code."""
    result: dict[str, str | None] = {"code": None, "error": None}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            state = (qs.get("state") or [""])[0]
            code = (qs.get("code") or [""])[0]
            if state != expected_state or not code:
                result["error"] = "Invalid OAuth state or missing code"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authentication failed: invalid state or missing code.")
            else:
                result["code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>EVE SSO Connected</h2>"
                    b"<p>You may close this tab and return to EVE Fitting Advisor.</p></body></html>"
                )
            done.set()

        def log_message(self, format: str, *args: Any) -> None:
            # Never log callback query strings (contain auth codes)
            return

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for EVE SSO callback")
    finally:
        server.shutdown()
        server.server_close()

    if result["error"] or not result["code"]:
        raise RuntimeError(result["error"] or "No authorization code received")
    return result["code"]
