"""Placeholder FastAPI app. The OpenAI-compatible API lands in Phase 1."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mini-ai-factory gateway")


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")
