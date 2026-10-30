"""Tests for the gateway's `/healthz` endpoint."""

import httpx
import pytest

from gateway.main import app


# Runs this async test on an event loop via anyio's pytest plugin, which is
# installed with FastAPI, so pytest-asyncio is not needed.
@pytest.mark.anyio
async def test_healthz_returns_ok() -> None:
    # ASGITransport sends requests straight into the app in-process: the full
    # FastAPI stack (routing, validation, serialization) runs, but no socket is
    # opened and no server is started. The base_url host is never resolved.
    # Note: it does not run the app's startup/shutdown (lifespan) hooks.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
