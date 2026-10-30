# Mini AI Factory: Build Roadmap

Oct 30, 2026 · @Connor

Six phases, Phase 0 starting now and Phase 5 landing by end of May 2027, each closing on a demoable gate. The companion plan doc holds what each layer is; this one holds how to build it, what to learn along the way, and where Claude Code fits.

## At a glance

&#91;embedded content: build timeline · 6 phases, Oct 2026 to May 2027\]

Phases run back to back; each gate is a demo plus a merged ADR, and slipping a gate pushes everything after it.

## Phase 0: Foundation (now to Nov 8)

Gate: an empty repo that already has CI, a `CLAUDE.md`, a dev container, and one passing test. Everything after this is faster because of it.

**Set up**

1. Install Docker Desktop, `uv` (Python package manager), `git`, Claude Code, and VS Code with the Python and Dev Containers extensions. `uv` over pip/venv because it resolves and installs in seconds and pins a lockfile, which matters once four services share one repo.
2. Create the `mini-ai-factory` repo on GitHub. Layout: `services/` (one folder per layer), `infra/` (docker-compose, later Kubernetes), `docs/adr/`, `tests/`. A monorepo beats five repos here because the layers share telemetry conventions and a single CI run proves they still work together.
3. Write `CLAUDE.md` at the root. This is the single highest-leverage file in the project: it tells Claude Code your conventions (Python 3.12, `uv`, Pydantic v2, pytest, type hints everywhere, `ruff` for lint/format, ADRs for every design decision, never commit secrets). Add a `Makefile` or `justfile` with `make test`, `make lint`, `make up` so Claude Code and you run the same commands.
4. GitHub Actions: lint + test on every PR. Add `gitleaks` or `trufflehog` as a pre-commit hook on day one. Secret scanning is the cheapest security win in the whole project and it is embarrassing to add after the first leaked token.
5. Write ADR-000: why a monorepo, why `uv`, why FastAPI. Two paragraphs. The habit matters more than the content.

**The working loop you will reuse every phase**

- Open Claude Code in the repo. Start each work session by describing the slice you want ("add a `/v1/chat/completions` endpoint that proxies to Ollama, with a test") and asking it to plan first, then implement. Review the plan before approving; that review is where you learn.
- Commit in small slices behind a feature branch and open a PR even though you are solo. Ask Claude Code to review its own PR as a hostile reviewer. You will catch things.
- After a slice works, ask Claude Code to explain one thing you did not fully follow. Then write it into the ADR or README in your own words. If you cannot explain it, you do not own it yet.

**Security habit from day one**: a `.env.example` with placeholder values, real secrets in `.env` (gitignored), and a `SECURITY.md` listing the threat model you are building toward. Update it each phase.

## Phase 1: Inference service and telemetry (Nov 9 to Dec 20)

Gate: `make up` starts the model, the gateway and Grafana; a k6 run produces a latency/throughput chart that lives in the README.

**Build order**

1. Pull a small model with Ollama (Llama 3.x 8B or Qwen 2.5 7B at 4-bit quantization). Why quantized: a 7B model in 4-bit fits in roughly 5 GB of RAM and runs on a laptop CPU at a few tokens/sec, enough to measure everything that matters. The tradeoff is some quality loss, which you do not care about here.
2. FastAPI gateway in `services/gateway/`. Expose OpenAI-compatible `/v1/chat/completions`. Why that shape: every client library and every later layer already speaks it, so the gateway becomes a drop-in. Make it async from the start; a sync handler blocks the event loop during a 20-second generation.
3. Streaming via server-sent events. Learn how `StreamingResponse` and async generators interact. Time-to-first-token is only measurable once you stream.
4. OpenTelemetry: one trace per request, spans for `tokenize`, `generate`, `tool_call`. Metrics as histograms, not averages. Why histograms: p50 tells you nothing about the user who waited 30 seconds; p95/p99 is what an SLO is written against.
5. Docker-compose with Prometheus, Tempo and Grafana. Build one dashboard: tokens/sec, TTFT p50/p99, queue depth, requests in flight.
6. Per-tenant token budgets via an API key header and a token-bucket rate limiter in Redis. Why Redis rather than in-memory: it survives a gateway restart and works when you run two gateway replicas in Phase 4.
7. Request batching. Collect requests for a few milliseconds, then send them as one batch. Measure what batch size does to p99 at fixed throughput. This is the single most interesting experiment in the project: throughput and latency trade against each other, and you will see the curve.
8. Load test with k6. Publish the chart.

**Security angle**: API keys hashed at rest (never stored plaintext), per-tenant isolation in logs so one tenant's prompts never appear in another's traces, and a note in `SECURITY.md` about prompt data in telemetry: span attributes should carry token counts, never prompt text.

**Tradeoffs worth an ADR**: Ollama vs vLLM (simplicity vs real batching), SSE vs WebSockets, Redis vs in-process limiter.

**Claude Code prompts that work here**: "Instrument this endpoint with OTel following the conventions in CLAUDE.md, and explain why you chose histogram buckets of X." Ask it to generate the k6 script and the Grafana dashboard JSON; those are tedious and well-suited to it.

## Phase 2: Network ops MCP server (Dec 21 to Jan 31)

Gate: from Claude Desktop, ask "which interfaces on the sandbox switch are seeing errors?" and get an answer sourced from live NX-API data, with a mutating tool that refuses to run without approval.

**Build order**

1. Get a DevNet account and hit the always-on NX-OS sandbox with `curl` first. Understand NX-API's JSON-RPC shape and RESTCONF's YANG-modelled paths before writing any code. Why: the hard part of this layer is the data model, not Python.
2. Write a thin client in `services/netops/client.py` with Pydantic models for interfaces, BGP neighbors and counters. Validation at the boundary means every tool downstream gets typed, predictable data. The tradeoff is upfront modelling time; it pays back the first time a sandbox response changes shape.
3. Build the MCP server with the official Python SDK. Start with three read-only tools. Learn how MCP separates tools, resources and prompts, and why the protocol is stdio or streamable HTTP rather than a REST API: it is designed for a model to discover capabilities at runtime.
4. Add one mutating tool (`set_interface_description`) behind an approval gate: the tool returns a pending-change ID; a separate `approve_change` tool, which the agent cannot call without a human-supplied token, executes it. This is the same pattern as Cisco AI Defense's agent-action controls, and it is the pattern you will red-team in Phase 3.
5. Caching (short TTL, keyed by device + command) and rate limiting so the agent cannot hammer the shared sandbox.
6. Wire the MCP server to your Phase 1 gateway so the whole path is yours: client to gateway to model to MCP to switch.
7. Stretch: Containerlab with SR Linux so you can inject faults (shut an interface, break BGP) and confirm the agent diagnoses them.

**Security angle**: least privilege on the sandbox credentials, every tool call logged with who/what/when into your OTel pipeline, and tool outputs treated as untrusted input (that becomes the whole point of Phase 3).

**Tradeoffs worth an ADR**: NX-API vs RESTCONF vs pyATS, stdio vs HTTP transport for MCP, approval token vs allowlist.

**Claude Code prompts**: have it scaffold the MCP server and the Pydantic models from a saved sample of real NX-API JSON. Ask it to write the approval-gate design as a sequence diagram in the ADR before coding it.

## Phase 3: Red team and guardrails (Feb 1 to Mar 14)

Gate: a `make redteam` run prints attack success rate before and after guardrails, and the README has the OWASP LLM Top 10 / MITRE ATLAS mapping table.

**Build order**

1. Write the threat model first, in `SECURITY.md`: assets (switch config, sandbox credentials, tenant prompts), attackers (a malicious user, a poisoned data source, a compromised tool), and trust boundaries. Why first: a red-team suite without a threat model is a pile of random prompts.
2. Build the attack harness in `tests/redteam/` as parametrized pytest cases. Each case has a goal, a payload, and an oracle that decides whether the attack succeeded (did the mutating tool fire, did a secret leave the system, did the model claim an action it did not take).
3. Attack families: direct prompt injection, indirect injection through tool output (seed the sandbox interface description with instructions), privilege escalation (coax the agent into calling `approve_change` with a guessed token), exfiltration (a "send report" tool that posts to a URL the attacker supplies), and denial of wallet (loop the agent into burning the tenant budget).
4. Run it against the Phase 2 agent with no defences. Record the baseline. Expect it to be bad; that is the point.
5. Defences in layers, measuring after each: input classifiers, NeMo Guardrails Colang rails for topic and action control, output filters that strip URLs and secrets, and strict tool-output sanitization (tool results wrapped as data, never injected into the system prompt). Learn why no single layer suffices: each one fails on a different attack family.
6. Map every case to OWASP LLM Top 10 and MITRE ATLAS technique IDs in a table.
7. Add the harness to CI so a regression in guardrails fails the build.

**Why this is the strongest piece**: Cisco AI Defense now ships a self-service red-teaming edition and an Agent Runtime SDK. You will have built both at small scale and can speak concretely to what they should block, what they cannot, and what it costs in latency.

**Tradeoffs worth an ADR**: classifier vs rules vs LLM-as-judge for detection (speed, cost, false positives), fail-open vs fail-closed when a guardrail errors.

**Claude Code prompts**: have it generate attack cases from your threat model, then play adversary: "here is my guardrail config, find three ways around it." Have it draft the Colang rails and explain the grammar.

## Phase 4: Kubernetes, Cilium and eBPF (Mar 15 to Apr 25)

Gate: the full stack runs on a local kind cluster; a Hubble flow log shows the Phase 3 exfiltration attempt dropped at the network layer, and a bpftrace script shows syscalls from the agent pod.

**Build order**

1. Learn the Kubernetes mental model before touching YAML: Pods, Deployments, Services, Namespaces, and the reconciliation loop. Why it matters: Kubernetes is a control plane that continuously drives actual state toward declared state. Once that clicks, every manifest makes sense.
2. Create a kind cluster with the default CNI disabled, then install Cilium as the CNI. Why Cilium: it replaces iptables with eBPF programs loaded into the kernel, which gives identity-based policy (by pod label, not IP) and L7 visibility without a sidecar. This is the Isovalent tech Cisco bought.
3. Containerize each service, write manifests (or a Helm chart) in `infra/k8s/`, deploy gateway, model, MCP server, Redis and the observability stack. Learn why the model pod gets a resource request and limit, and what happens when it does not.
4. Default-deny `CiliumNetworkPolicy` in the namespace, then allow only: gateway to model, gateway to MCP server, MCP server to the DevNet sandbox CIDR, everything to Redis and the OTel collector. Nothing to the public internet.
5. Enable Hubble. Rerun the Phase 3 exfiltration case. Capture the dropped flow and the L7 HTTP policy verdict. That screenshot goes in the README.
6. Add an L7 policy: the MCP server may only call `POST /ins` on the sandbox, nothing else. Learn why L7 policy is possible here without a proxy sidecar (Cilium's Envoy is shared and eBPF-steered).
7. eBPF directly: one bpftrace script that counts `execve` and `connect` syscalls from the agent container's PID namespace. Then a second that alerts on any `connect` to a non-allowlisted address. This is a miniature of runtime security tooling like Tetragon, and you can name that in interviews.

**Security angle**: this phase turns the application-layer defences of Phase 3 into infrastructure-layer ones. The point to articulate: a guardrail bypass still cannot exfiltrate if the network says no. Defence in depth, demonstrated.

**Tradeoffs worth an ADR**: kind vs k3d vs minikube, Helm vs Kustomize, identity-based vs IP-based policy, Cilium L7 vs a service mesh.

**Claude Code prompts**: have it write the manifests and policies, then ask "walk me through what the kernel does when a packet from the gateway pod reaches the model pod under this policy." It is good at explaining eBPF datapaths. Verify with Hubble.

## Phase 5: Console, capacity planner, and the demo (Apr 26 to May 31)

Gate: a console page showing per-tenant spend, p99 latency, guardrail block rate and policy drops, plus a capacity planner that answers "how many replicas for N users at a 2-second TTFT SLO, and what is cost per million tokens." A 3-minute recorded demo and a polished README close the project.

**Build order**

1. Console as a small FastAPI + HTMX app (or a Grafana dashboard with a custom panel if you want to stay lean). Why HTMX: no front-end build chain, and the point is the numbers, not the UI.
2. Data sources: Prometheus for latency and throughput, Redis for tenant budgets, your Phase 3 harness output for block rates, Hubble's API for policy drops. One query layer, one page.
3. Capacity planner: fit a simple model to your Phase 1 throughput curve (tokens/sec per replica as a function of batch size and concurrency). Inputs: users, requests per user per hour, tokens per request, SLO. Output: replicas needed and cost per million tokens using a published GPU hourly price. Show the sensitivity: what happens to cost when the SLO loosens from 2 s to 4 s. This is the MBA on display.
4. Write the final README as a product narrative: problem, who it is for, architecture diagram, what each layer proves, metrics, what you would build next. Link the ADRs.
5. Record the demo: inject the agent, watch guardrails block it, then show Hubble dropping the exfiltration anyway, then the console with the cost numbers. Three minutes, no slides.
6. Write a short post for LinkedIn and send the repo to two or three Cisco or Fuqua alumni contacts for feedback before recruiting season.

**Tradeoffs worth an ADR**: analytical capacity model vs simulation, HTMX vs React, cloud GPU price assumptions.

**Claude Code prompts**: have it build the HTMX page and the Prometheus queries, and have it critique your README as a Cisco hiring manager would. Then rewrite the README yourself.

## Using Claude Code well, and where not to lean on it

Claude Code is the pair programmer; you are the engineer of record. The split that keeps the rust coming off while still shipping:

| You do | Claude Code does |
| --- | --- |
| Decide the architecture and write the ADR | Scaffold services, Dockerfiles, manifests |
| Write the threat model and pick attack families | Generate attack cases and the pytest harness |
| Design the approval gate and policy rules | Implement them and write the tests |
| Read every PR diff before merging | Review its own PR as a hostile reviewer first |
| Explain each phase in the README in your words | Draft dashboards, k6 scripts, boilerplate |
| Debug anything you do not understand | Explain the how and why on request |

**Habits that make it work**

- Keep `CLAUDE.md` current. When Claude Code does something you dislike, fix the convention there rather than correcting it each time.
- Ask for a plan before code on anything over 50 lines. Approve or edit the plan; that is where the thinking happens.
- Use it as a tutor deliberately: "explain why async matters here", "what would break if I used a list instead of a deque", "what does this eBPF program do line by line." One deep question per session beats ten shallow ones.
- Never let it commit secrets, and never paste real credentials into a prompt. Your pre-commit scanner from Phase 0 is the backstop.

**Where not to lean on it**

- The ADRs and the README analysis. If an interviewer asks why you chose X and you cannot answer without the doc, the project loses its value.
- The threat model. It can expand yours, but the first draft should be your own reasoning about what you are protecting.
- Anything you will demo live. Rehearse it yourself until the commands are muscle memory.

## Learning resources by phase

Read the one marked first before the phase starts; the rest as you hit the need.

| Phase | Read first | Then, as needed |
| --- | --- | --- |
| 0 | Claude Code docs on `CLAUDE.md` and memory | GitHub Actions quickstart; gitleaks README |
| 1 | FastAPI async docs; OpenTelemetry Python getting started | Ollama API reference; k6 docs; Prometheus histogram semantics; "Tail at Scale" (Dean & Barroso) for why p99 matters |
| 2 | MCP specification and Python SDK README | Cisco DevNet NX-API Learning Labs; RESTCONF/YANG primer on DevNet; Containerlab quickstart |
| 3 | OWASP Top 10 for LLM Applications | MITRE ATLAS; NeMo Guardrails docs (Colang); Simon Willison's prompt injection posts; Cisco AI Defense product pages for the commercial framing |
| 4 | Kubernetes "Concepts" section; Cilium getting started on kind | Hubble docs; Cilium L7 policy examples; Liz Rice, *Learning eBPF* (free from Isovalent); bpftrace one-liners |
| 5 | HTMX docs | Prometheus query reference; any published GPU cloud pricing page for the cost model |
