# ADR-000: Monorepo and tooling

- Status: Accepted
- Date: 2026-10-30

## Context

mini-ai-factory is built in layers over several months: an inference gateway,
telemetry, a network-ops MCP agent, guardrails with a red-team harness, then
Kubernetes with Cilium. Each layer is its own service, but they are not
independent:

- They share conventions: the same OpenTelemetry attribute names, the same
  Pydantic models at their boundaries, the same rules about what must never be
  logged.
- Later layers call earlier ones (MCP agent to gateway to model), so a change in
  one can break another.
- This is a security project. How the repo itself is built (lockfiles, CI
  permissions, pinned actions) is part of what it demonstrates.
- One developer, one laptop, plus Claude Code. What runs locally must run the
  same way in CI and later in containers, and the human and Claude Code must
  use the same commands.

So the tooling has to keep several services consistent with each other, make
installs reproducible across laptop, CI and container, be fast enough that
running checks is never a chore, and be secure by default.

## Decision

| Area            | Choice                                                                                 |
|-----------------|----------------------------------------------------------------------------------------|
| Repository      | One monorepo, one folder per service under `services/`                                 |
| Packaging       | uv workspace: one `uv.lock`, one `.venv`, each service its own package                 |
| Python          | 3.12                                                                                   |
| Services        | FastAPI on Uvicorn, `async def` handlers, Pydantic v2 for all external data            |
| Lint and format | Ruff, including async (`ASYNC`) and security (`S`) rules                               |
| Tests           | pytest in `importlib` mode; async tests via the anyio plugin and `httpx.ASGITransport` |
| Commands        | Makefile: `setup`, `lint`, `format`, `test`                                            |
| CI              | GitHub Actions on every PR: same Make targets, locked deps, read-only token, SHA pins  |
| Config          | Environment variables; `.env` gitignored, documented in `.env.example`                 |

The sections below explain how each piece works, what it costs, and what else
was considered.

---

## 1. One monorepo

### How it works

All services live in one git repository:

```text
services/   one folder per layer (gateway now; netops, guardrails, console later)
infra/      docker compose, later Kubernetes manifests      (not created yet)
tests/      tests that span services, e.g. the red-team     (not created yet)
docs/adr/   one record per design decision
```

A change that touches two services, such as renaming a telemetry attribute in
both the gateway and the MCP agent, is one commit and one PR, and one CI run
tests both together.

### Tradeoffs

- **Pro: atomic cross-service changes.** No "release a shared library, then bump
  it in three repos in the right order".
- **Pro: one CI pipeline** proves the layers still work together.
- **Con: CI runs everything on every PR.** That costs seconds today. If it gets
  slow, path filters can skip work for services a PR didn't touch.
- **Con: easy to couple services by accident**, for example the netops service
  importing gateway code instead of calling its API. Rule: services talk over the
  network, not through imports. Code that really is shared goes in a deliberate
  shared package (see "Revisit when").
- **Con: one repo is not one runtime.** Each service still ships as its own
  container image with only its own dependencies. Section 2 covers how the
  workspace supports that.

### Alternatives

- **One repo per service (polyrepo).** Real isolation and independent release
  cycles, which matter when separate teams own separate services. With one
  owner it is mostly overhead: N CI configs, N lockfiles, and cross-service
  changes split across PRs that must merge in order.

---

## 2. uv workspace for packaging

This decision has the most moving parts. It helps to separate the three jobs
that Python tooling has historically spread across several tools:

1. **Resolve.** Given "fastapi 0.162 or newer", pick exact versions of fastapi
   and every package it depends on, all compatible with each other
   (traditionally pip-tools or Poetry).
2. **Install.** Put those exact versions into an isolated environment
   (traditionally pip plus venv).
3. **Provide Python itself.** Get the right interpreter version (traditionally
   pyenv).

uv does all three. It is written in Rust and keeps a global download cache, so
it is fast enough that "make the environment match the lockfile before every
command" costs milliseconds. Most of the design below depends on that speed.

### Key files

```text
pyproject.toml          root: workspace definition, dev tools, ruff + pytest config
uv.lock                 exact versions and hashes for every package of every member
.python-version         "3.12": which interpreter uv uses
.venv/                  the one environment (gitignored, rebuilt from uv.lock)
services/gateway/
  pyproject.toml        the gateway's own runtime dependencies
  src/gateway/          the importable package
  tests/
```

### pyproject.toml states intent, uv.lock records the result

`pyproject.toml` says what a package needs, as ranges: "the gateway needs
`fastapi>=0.162.0`". Ranges allow upgrades.

`uv.lock` records what the resolver picked: fastapi 0.162.0, starlette 1.10.0,
pydantic-core 2.50.2 and so on, each with a sha256 hash of the exact file to
download. Everyone who installs from the same lockfile gets byte-identical
packages. If the package index ever served a different file for the same
version, the hash check would fail the install instead of running it.

The lockfile is **universal**. It is resolved once for every platform and every
Python version allowed by `requires-python = ">=3.12"`, not just for the
machine that ran the resolve. The same `uv.lock` works on a WSL laptop, a Linux
CI runner and a container image. (pip-compile, by contrast, produces output for
the platform it ran on.)

`uv.lock` is committed. `.venv/` is not; it is a disposable product of the
lockfile.

### What happens when you run `make test`

```text
make test
└─ uv run pytest
     1. lock   Has any pyproject.toml changed since uv.lock was written?
               If so, re-resolve and rewrite uv.lock.
               (CI sets UV_LOCKED=1, which makes this an error instead.)
     2. sync   Make .venv match uv.lock exactly: install what is missing,
               remove what is not in the lock.
     3. run    Run pytest from .venv.
```

Because every command goes through `uv run`, nobody activates a virtualenv or
remembers to reinstall after pulling. Step 2 is normally a no-op.

### What a workspace adds

A **workspace** is several packages that share one lockfile and one `.venv`.
The root `pyproject.toml` declares the members:

```toml
[tool.uv.workspace]
members = ["services/*"]
```

Each folder under `services/` is a member: a real Python package with its own
`pyproject.toml` listing its own runtime dependencies. The gateway lists fastapi
and uvicorn; the netops service will list the MCP SDK. Resolution runs once
across all members together, so if two services both use pydantic, they get the
same version.

Members are installed **editable**. `.venv` contains a file called
`gateway.pth` that points at `services/gateway/src`, so Python imports the
gateway straight from your working tree and edits take effect without
reinstalling.

### Three non-obvious settings in the root pyproject.toml

**`package = false` makes the root virtual.** The root `pyproject.toml` needs a
`[project]` table so it can declare dependencies, but there is no
`mini_ai_factory` Python package to build. `package = false` tells uv never to
build or install the root: it only holds the workspace, the dev tools and the
repo-wide config. In `uv.lock` the root appears as `source = { virtual = "." }`
and the gateway as `source = { editable = "services/gateway" }`.

**The root depends on every member.**

```toml
dependencies = ["gateway"]

[tool.uv.sources]
gateway = { workspace = true }
```

`uv sync` is exact: it removes anything the project being synced doesn't need.
A virtual root that depends on nothing therefore needs nothing, and a plain
`uv sync` uninstalls the gateway (verified on uv 0.13.6). The workaround would
be passing `--all-packages` to every command, which works until someone, or
Claude Code, runs a bare `uv sync` and imports start failing for no obvious
reason. Listing the members as dependencies makes the obvious command the
correct one.

`{ workspace = true }` tells uv that `gateway` means the local member and that
it must not look for it on PyPI. That also means a PyPI package that happens to
be called `gateway` can never be installed in its place.

The cost: one line in `dependencies` and one in `[tool.uv.sources]` for each
new service.

**Dev tools live in a dependency group.**

```toml
[dependency-groups]
dev = ["httpx>=0.28.1", "pytest>=9.2.0", "ruff>=0.17.3"]
```

Dependency groups (PEP 735) hold dependencies for working on the project that
are never part of any package's runtime requirements. uv installs the `dev`
group by default, so it is present on your laptop and in CI; `--no-dev` leaves
it out, which is what the Dockerfiles will use. ruff and pytest never end up in
a container image.

`httpx` is here only because the tests use it. When the gateway starts calling
the model server over HTTP in Phase 1, it moves into the gateway's own
dependencies.

### Member layout: src layout and uv_build

The gateway's code lives in `services/gateway/src/gateway/`, not
`services/gateway/gateway/`. The extra `src/` level means the package can only
be imported once it is installed, never by accident because it happens to sit in
the current directory. Tests therefore exercise the package the way a container
will see it, and packaging mistakes show up in tests instead of in production.

`uv_build` is the **build backend**: the tool that turns a source folder into an
installable package (here, the editable install). It is uv's own, so there is no
extra build tool to download, and it expects exactly this layout
(`src/<name>/__init__.py`). It handles only pure-Python packages, which is all
this project has.

### Python 3.12

`.python-version` contains `3.12`, and uv uses it to pick the interpreter. It
pins the minor version, not the patch: on this laptop `.venv` uses uv's own
managed CPython 3.12 build (in `~/.local/share/uv/python/`), while a CI runner
may use its system 3.12. Patch releases contain only bug and security fixes.

Why 3.12 rather than the newest Python: every dependency ships prebuilt wheels
for it, it is a common base for container images (`python:3.12-slim`), and it
gets security fixes until October 2028. `requires-python = ">=3.12"` keeps the
lockfile valid for newer versions, so upgrading later is a one-line change to
`.python-version` plus a test run.

### Tradeoffs

- **Pro: fast, reproducible, one tool.** uv replaces pip, venv, pip-tools and
  pyenv, and syncing is fast enough to do before every command.
- **Pro: one resolution means consistent versions** across services: one
  pydantic, one OpenTelemetry SDK everywhere.
- **Con: one resolution means every service must agree.** If two services ever
  need incompatible versions of the same package, one workspace cannot express
  it. The escape hatch is moving that service out into its own project with its
  own lockfile.
- **Con: the shared `.venv` hides missing dependencies.** Because services share
  one environment, netops could `import fastapi` without declaring it and its
  tests would still pass locally, since the gateway installed fastapi. The
  container build catches this: `uv sync --package netops --frozen --no-dev`
  installs only what netops declares, so the undeclared import fails there.
- **Con: tool and vendor concentration.** uv is young compared with pip, comes
  from a single company (Astral), and `uv.lock` is a uv-specific format.
  Mitigation: apart from the `[tool.uv.*]` tables, everything is standard
  `pyproject.toml` (PEP 621 project metadata, PEP 735 dependency groups), and
  `uv export --format pylock.toml` writes the standard lockfile format
  (PEP 751). Leaving uv means generating a new lockfile, not rewriting the
  project.
- **Con: the `services/*` glob is strict.** Any non-empty folder under
  `services/` without a `pyproject.toml` makes every uv command fail with
  "Workspace member ... is missing a `pyproject.toml`". Notes and scratch files
  go somewhere else.

### Alternatives

- **pip, venv and pip-tools.** The standard building blocks, understood
  everywhere. But that is three tools, a manual activate-and-reinstall loop,
  platform-specific lock output, no workspace concept, and much slower
  installs.
- **Poetry.** Mature, with a lockfile. But no native workspaces and a slower
  resolver, and it is a second tool when uv already covers the job.
- **One flat project** with every service's dependencies in one
  `pyproject.toml`. The simplest option, but every container image would carry
  every service's dependencies: the gateway image would include the MCP SDK and
  the netops image the model client. Images get bigger, and each one's attack
  surface includes code it never uses.
- **A separate uv project per service**, with no workspace. Maximum isolation,
  but N lockfiles to update and N environments to keep in sync, and cross-service
  tests like the red-team harness need several projects installed into one
  environment anyway.

---

## 3. FastAPI on Uvicorn, async by default

### How it works

A web service here has two parts:

- **Uvicorn** is the server. It owns the network socket, parses HTTP, and hands
  each request to the application through **ASGI**, the asynchronous standard
  interface between Python web servers and apps. ASGI is the async successor to
  WSGI, the interface Flask and Django were built on.
- **FastAPI** is the application framework: routing, request and response
  validation, generated API docs. It is built on Starlette (the ASGI toolkit)
  and Pydantic (validation).

The placeholder app shows the pattern every endpoint will follow:

```python
class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")
```

FastAPI reads the return type annotation and uses `HealthResponse` as the
response model: it validates the output and documents the schema at `/docs`.
Request bodies work the same way in reverse: declare a Pydantic model as a
parameter, and malformed input is rejected with a 422 response before your code
runs. That is the CLAUDE.md rule "Pydantic v2 for all external data" in
practice: untyped JSON never gets past the edge of a service.

### Why async matters for this project

An inference gateway spends most of its time waiting: on the model server (a
generation can take 20 seconds), on Redis, on the telemetry exporter. An
`async def` handler runs on an **event loop**. At each `await` it hands control
back to the loop, which runs other requests while this one waits. One process
can hold many slow requests at once without a thread per request.

The catch: a blocking call inside an `async def`, such as `time.sleep()`,
`requests.get()` or a plain file read, freezes the whole event loop, so every
in-flight request stalls, not just that one. FastAPI runs plain `def` handlers
in a thread pool to soften this, but mixing styles makes it easy to get wrong.
So: handlers are `async def`, I/O goes through async libraries (httpx's
`AsyncClient`, `redis.asyncio`), and Ruff's `ASYNC` rules flag known blocking
calls (section 4).

### Tradeoffs

- **Pro: async-native**, which fits a service that mostly waits on other
  services.
- **Pro: Pydantic at the boundary.** One model definition gives validation,
  typed code and API docs. The OpenAI-compatible `/v1/chat/completions` request
  and response shapes become Pydantic models in Phase 1.
- **Pro: ecosystem.** OpenTelemetry has FastAPI auto-instrumentation, and most
  LLM-serving examples use FastAPI.
- **Con: FastAPI is still 0.x** (0.162.0 at the time of writing). Under semantic
  versioning, a 0.x minor release may contain breaking changes. The lockfile
  prevents surprise upgrades; read the changelog when bumping it.
- **Con: async is less forgiving.** One blocking call degrades every request, and
  it shows up as latency under load rather than as an error. Lint rules catch
  the common cases, not all of them.

### Alternatives

- **Flask.** Simple and well known, but built on WSGI: each in-flight request
  holds a worker thread or process, and Flask's async views still occupy a
  worker for the whole request. Holding hundreds of 20-second generations open
  means hundreds of workers.
- **Django.** Brings an ORM, admin site and auth system that a stateless gateway
  doesn't need, and async support is still uneven across its ecosystem.
- **Litestar.** Similar design to FastAPI and technically strong, with a smaller
  ecosystem and fewer examples to learn from.
- **Starlette alone.** What FastAPI is built on; you would hand-write the
  validation and docs FastAPI generates.

---

## 4. Ruff for lint and format

### How it works

Ruff is one tool, written in Rust, that replaces flake8 and its plugins, isort
and Black:

- `ruff check` finds problems (linting); `ruff check --fix` applies safe
  automatic fixes.
- `ruff format` rewrites code layout in a Black-compatible style. It also
  formats Python code blocks inside Markdown files, including these ADRs.

Ruff's defaults enable only a small core of rules (`E4`, `E7`, `E9`, `F`). This
repo selects more:

| Code  | Origin         | Catches                                                                                                                                      |
|-------|----------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| E, W  | pycodestyle    | Style errors and warnings                                                                                                                    |
| F     | Pyflakes       | Real bugs: undefined names, unused imports and variables                                                                                     |
| I     | isort          | Import ordering                                                                                                                              |
| B     | flake8-bugbear | Likely bugs, e.g. a mutable default argument `def f(x=[])` (B006)                                                                            |
| UP    | pyupgrade      | Syntax that is outdated for Python 3.12, e.g. `Optional[str]` instead of `str \| None`                                                       |
| RUF   | Ruff           | Assorted correctness checks specific to Ruff                                                                                                 |
| ASYNC | flake8-async   | Blocking calls in `async def`: `time.sleep` (ASYNC251), blocking HTTP clients (ASYNC210), `open()` (ASYNC230)                                |
| S     | flake8-bandit  | Security smells: hardcoded passwords (S105), binding to all interfaces (S104), HTTP requests without a timeout (S113)                         |

`ASYNC` and `S` are the two chosen specifically for this project: one enforces
the async rule from section 3, the other is a cheap first pass of security
review on every change.

Two configuration details:

- **Tests may use `assert`.** Bandit flags `assert` (S101) because Python strips
  asserts when run with `-O`, so an assert used as a security check silently
  disappears. In tests, `assert` is exactly what pytest wants, so S101 is turned
  off for `**/tests/**`.
- **`src = ["services/*/src"]`** tells Ruff where first-party code lives, so
  `import gateway` is grouped as "our code" rather than "third-party". Without
  it, the import-sorting rule fails on the gateway's test file (verified).

### Tradeoffs

- **Pro: speed and one tool.** Linting and formatting the whole repo takes well
  under a second, so it can run on every save and every PR without anyone
  minding.
- **Con: no type checking.** Ruff reads syntax, not types. A type checker is a
  separate tool (see "Revisit when").
- **Con: bandit-style rules are pattern matching, not analysis.** They produce
  some false positives and miss anything subtle. A false positive is silenced
  with a line comment such as `# noqa: S104` plus the reason (for example, a
  container that must listen on `0.0.0.0`), which keeps every exception visible
  in review. These rules complement the red-team work; they don't replace it.

### Alternatives

- **flake8, its plugins, isort and Black.** The same checks spread across four
  tools with four configurations, and an order of magnitude slower.
- **Pylint.** Deeper analysis, but slow and noisy by default.

---

## 5. pytest

### How it works

- **Where tests live.** Unit tests sit with their service
  (`services/gateway/tests/`), so a service's code and tests move together. The
  root `tests/` is for tests that span services, like the red-team harness.
  `testpaths = ["services", "tests"]` makes pytest look in both.
- **Async tests.** anyio, which comes with FastAPI via Starlette, includes a
  pytest plugin. `@pytest.mark.anyio` runs an `async def` test inside an event
  loop, so no extra plugin such as pytest-asyncio is needed.
- **No server needed.** `httpx.ASGITransport` connects an httpx client directly
  to the ASGI app in the same process. Each request goes through the full
  FastAPI stack (routing, validation, serialization) without opening a socket or
  starting Uvicorn, which keeps tests fast and deterministic:

  ```python
  @pytest.mark.anyio
  async def test_healthz_returns_ok() -> None:
      transport = httpx.ASGITransport(app=app)
      async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
          response = await client.get("/healthz")

      assert response.status_code == 200
  ```

- **`--import-mode=importlib`.** pytest's historical default, `prepend`, adds
  each test file's folder to `sys.path` and imports the file by its bare name.
  Two services that each have `tests/test_healthz.py` would then both be module
  `test_healthz`, and pytest stops with an error. `importlib` mode imports each
  test file without touching `sys.path`, so duplicate file names are fine.
  pytest's own documentation recommends it for new projects. The cost: test
  files can't import each other (`from test_helpers import ...`). Shared
  fixtures go in `conftest.py`, which pytest loads automatically.
- **`--strict-markers`** turns a typo like `@pytest.mark.anyoi` into an error
  instead of a warning. **`--strict-config`** does the same for unknown pytest
  settings.

### Tradeoffs

- **`ASGITransport` does not run the app's lifespan** (its startup and shutdown
  hooks). The placeholder has none. Once the gateway creates resources at
  startup, such as an HTTP connection pool or a Redis client, tests must start
  the lifespan explicitly, for example with the `asgi-lifespan` package's
  `LifespanManager` or FastAPI's `TestClient` used as a context manager.
- **In-process tests skip the real server, network and container.** Those are
  covered by integration tests against `make up` once it exists.

### Alternatives

- **unittest** from the standard library. No dependency, but more boilerplate,
  and nothing comparable to pytest's fixtures and parametrization, which the
  red-team harness depends on.
- **FastAPI's `TestClient`.** A synchronous wrapper and the simplest option for
  sync tests. Async tests with httpx follow the "async by default" rule and use
  the same client library the gateway will use in production.

---

## 6. Makefile as the shared command interface

```text
make setup    uv sync (pre-commit hooks are added in a follow-up PR)
make lint     ruff check + ruff format --check    changes nothing
make format   ruff check --fix + ruff format      rewrites files
make test     pytest
```

The point is one shared vocabulary. You, Claude Code (told to via CLAUDE.md) and
CI all run `make lint` and `make test`, so "passes locally" and "passes in CI"
mean the same thing. When a command changes, it changes in one place.

`lint` is check-only on purpose: CI must report problems, not quietly fix them
in a throwaway checkout. `format` is the local counterpart that fixes them.

### Tradeoffs

Make is preinstalled on Linux and macOS and widely understood. Its syntax is
quirky: recipe lines must be indented with a tab, and targets that aren't files
must be listed under `.PHONY` (otherwise a file named `test` would make
`make test` report "up to date" and do nothing). It isn't native on Windows,
which doesn't matter under WSL.

### Alternatives

- **just.** The same idea with cleaner syntax and no `.PHONY` trap, but one more
  tool to install everywhere.
- **Raw uv commands in docs and CI.** Works, but the commands drift apart
  between the README, CI and your shell history.

---

## 7. CI: GitHub Actions on pull requests

### How it works

`.github/workflows/ci.yml` runs on every pull request:

1. Check out the code.
2. Install uv with `astral-sh/setup-uv`, which also restores uv's download cache.
   The cache is keyed on a hash of `uv.lock` and the `pyproject.toml` files, so
   unchanged dependencies aren't downloaded again.
3. Run `uv sync`, then `make lint`, then `make test`.

On `pull_request` events GitHub checks out a temporary merge commit (the PR
branch merged into `main`), so CI tests what `main` would look like after the
merge, not the branch in isolation.

### Hardening, line by line

CI executes code from the PR: your code, plus every package `uv sync` installs.
Treat it as untrusted and give it as little as possible.

| Setting                                         | What it prevents                                                                                                                                                                                                                                                                                       |
|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `on: pull_request`, not `pull_request_target`   | For PRs from forks, `pull_request` runs with a read-only token and no secrets. `pull_request_target` runs with the base repo's secrets and a write token while it can be made to run the PR's code, which is a classic way public repos get compromised.                                               |
| `permissions: contents: read`                   | The automatic `GITHUB_TOKEN` can only read the repo. Even if a step is compromised, the token can't push code, edit releases or post comments.                                                                                                                                                         |
| `persist-credentials: false`                    | By default, checkout stores the token in `.git/config`, where every later step can read it, including any package `uv sync` installs. The job never needs git credentials after checkout, so they aren't kept.                                                                                        |
| Actions pinned to full commit SHAs              | A tag like `v10` is a movable pointer that whoever controls the action's repo can repoint. In March 2025 the tags of `tj-actions/changed-files`, used by tens of thousands of repos, were repointed to a malicious commit that dumped CI secrets into build logs. A commit SHA can't be moved; the `# v10.3.0` comment keeps it readable. |
| Pinned releases at least two weeks old          | Malicious releases are often caught and pulled within days. setup-uv v10.3.3 was two days old when this was written, so v10.3.0 (21 days old) was pinned instead.                                                                                                                                      |
| `UV_LOCKED: "1"`                                | Every uv command in the job fails if `uv.lock` doesn't match the `pyproject.toml` files, instead of silently re-resolving. This catches "changed a dependency, forgot to commit the lock"; without it CI would test versions nobody reviewed. Verified: with a stale lock, `uv sync` exits with code 1. |
| `timeout-minutes: 10`                           | The default job timeout is six hours; a hung test would burn runner minutes until then.                                                                                                                                                                                                                |
| `concurrency` with `cancel-in-progress: true`   | Pushing a new commit to a PR cancels the still-running check of the previous commit.                                                                                                                                                                                                                   |

### Tradeoffs

- **SHA pins don't update themselves.** Without automation they go stale and
  miss fixes. Follow-up: Dependabot (or Renovate) for `github-actions`, which
  updates SHA pins along with their version comments.
- **CI runs on PRs only, not on pushes to `main`.** That is enough while every
  change goes through a PR. Add `push: branches: [main]` for a status badge or
  to catch direct pushes.
- **No secret scanning yet.** The follow-up PR adds gitleaks twice: as a
  pre-commit hook, which stops a secret before it is committed, and in CI, which
  catches anything that got past the hook.

### Alternatives

- **GitLab CI, CircleCI and similar.** Equivalent for this purpose, but the repo
  lives on GitHub, and Actions needs no extra account or integration.

---

## 8. Configuration and secrets hygiene

- Services read configuration from environment variables, never from committed
  files. This works the same on a laptop, in docker compose and in Kubernetes,
  where values can come from Secrets.
- `.env` holds local values and is gitignored. `.env.example` is committed and
  documents every variable with a safe placeholder. It is empty for now because
  the placeholder gateway reads no config.
- `.gitignore` also excludes `*.pem` and `*.key` files, plus personal Claude Code
  overrides (`.claude/settings.local.json`, `CLAUDE.local.md`).
- Gitignore only prevents accidents with known file names. Secret scanning in
  the follow-up PR catches secrets pasted into ordinary files.

---

## Consequences

Day to day:

- Run `make setup` once after cloning, then `make lint` and `make test`. No
  virtualenv activation.
- Add a dependency to a service with `uv add --package gateway <name>`, and a
  dev tool with `uv add --dev <name>`. Commit `uv.lock` along with the change.
- Add a service by creating `services/<name>/` with its own `pyproject.toml` and
  `src/<name>/`, then adding it to the root `dependencies` and
  `[tool.uv.sources]`.
- Never edit `uv.lock` by hand.

## Revisit when

- **Two services need incompatible versions of a dependency.** Move one out of
  the workspace into its own project.
- **Services start sharing code**, such as common telemetry setup. Add a shared
  package, for example `libs/telemetry`, as a workspace member
  (`members = ["services/*", "libs/*"]`) rather than importing one service from
  another.
- **CI gets slow.** Add path filters per service, or split lint and tests into
  parallel jobs.
- **Type errors start slipping through.** Add a type checker such as mypy or
  pyright to `make lint`.
- **Dependency supply-chain hardening.** uv's `exclude-newer` setting accepts a
  duration such as `"1 week"` and then ignores packages uploaded within that
  window when resolving. It is the same cooldown idea as the pinned actions,
  applied to PyPI.
- **A service needs compiled extensions.** Switch that member's build backend
  from `uv_build` to hatchling or setuptools.
