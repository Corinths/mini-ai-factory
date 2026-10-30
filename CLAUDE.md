# mini-ai-factory

Laptop-scale model of an enterprise AI factory: inference gateway, telemetry,
network-ops MCP agent, guardrails/red team, Kubernetes + Cilium zero trust.

## Stack and conventions
- Python 3.12, managed with uv. Never use pip directly.
- FastAPI for services, Pydantic v2 for all external data, async by default.
- Type hints everywhere. Ruff for lint and format. Pytest for tests.
- Layout: services/<name>/ (one per layer), infra/, tests/, docs/adr/.
- Every design decision gets an ADR in docs/adr/NNN-title.md (context, decision, tradeoffs).

## Commands
- make setup   install deps and pre-commit hooks
- make lint    ruff check + format check
- make format  apply ruff autofixes and formatting
- make test    pytest
- make up      docker compose up (once it exists)

## Rules
- Plan before implementing anything over ~50 lines; wait for approval.
- Never commit secrets. Config comes from env vars; document them in .env.example.
- Never put prompt text or API keys in logs or telemetry span attributes.
- Small commits on feature branches; one PR per slice.
- When I ask "why", explain tradeoffs, not just the answer.

## Working agreement
- Audience: CS background, years away from daily coding. Skip basics; explain the
  how and why behind tools and designs, and their tradeoffs.
- Work in slices: plan first, wait for approval, one commit per slice on the phase branch.
- After each slice: run make lint and make test, summarize the diff, give one "why"
  worth understanding (2-4 sentences), then pause for a go-ahead.
- ADRs: create the file as a template with prompts; I write the content.
- Never run sudo or anything needing a browser login; print the exact command instead.
- Flag anything security-relevant as it comes up, unprompted.
- Environment: WSL2 Ubuntu 24.04, VS Code via WSL, Docker Desktop.