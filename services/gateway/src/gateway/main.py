"""Gateway HTTP application.

Placeholder for Phase 0: exposes only a liveness endpoint. The OpenAI-compatible
`/v1/chat/completions` API lands in Phase 1.

Run locally with `uv run uvicorn gateway.main:app --reload`; interactive API docs
are then served at http://127.0.0.1:8000/docs.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mini-ai-factory gateway")


class HealthResponse(BaseModel):
    """Body of the `/healthz` response.

    `Literal["ok"]` makes "ok" the only valid value, so FastAPI rejects any
    other output and the OpenAPI schema documents the exact contract.
    """

    status: Literal["ok"]


@app.get("/healthz")
async def healthz() -> HealthResponse:
    """Liveness probe: the process is up and serving requests.

    Deliberately checks no dependencies (model server, Redis). If it did, an
    outage in one of them would make Kubernetes restart healthy gateway pods.
    Dependency checks belong in a separate readiness endpoint.

    `async def` is the project default: handlers run on the event loop, so they
    must never make blocking calls (Ruff's ASYNC rules flag the common ones).
    The return annotation doubles as FastAPI's response model.
    """
    return HealthResponse(status="ok")
