"""Small HTTP client for the Designer handoff API."""

from __future__ import annotations

import httpx


class DesignerClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0, shared_secret: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.shared_secret = shared_secret

    async def create_handoff(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.shared_secret:
            headers["X-Agent-Secret"] = self.shared_secret
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/handoffs",
                json=payload,
                headers=headers,
            )
        if response.is_error:
            detail = response.text[:1000]
            raise RuntimeError(f"Designer handoff failed ({response.status_code}): {detail}")
        return response.json()

    async def session_exists(self, session_id: str) -> bool:
        """Check whether a handed-off Designer session still exists (returns True on transient errors)."""
        headers = {}
        if self.shared_secret:
            headers["X-Agent-Secret"] = self.shared_secret
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 5.0)) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/handoffs/sessions/{session_id}/exists",
                    headers=headers,
                )
            if response.status_code == 404:
                return False
            if response.is_success:
                return bool(response.json().get("exists", True))
        except Exception:  # noqa: BLE001
            return True
        return True
