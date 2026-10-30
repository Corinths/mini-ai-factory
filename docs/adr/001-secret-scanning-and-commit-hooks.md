# ADR-001: Secret scanning and commit hooks

- Status: Accepted
- Date: 2026-10-30

## Context

This project will handle several kinds of credentials: API keys for tenants of
the gateway (Phase 1), Cisco DevNet sandbox credentials (Phase 2), and whatever
tokens the CI and the cluster need later. Any of them could end up in a commit
by accident: pasted into a test while debugging, left in a config file, or
written into a notebook output.

In a public repository, a committed secret is a leaked secret. Three facts make
that hard to undo:

1. **Git history is permanent by design.** Deleting the file in a later commit
   leaves the secret in every earlier commit, in every clone, and in every fork.
2. **GitHub keeps pull request commits forever.** Every PR's commits stay
   reachable under a hidden reference (`refs/pull/<number>/head`) even after the
   branch is deleted or the history is rewritten and force-pushed. "Squash it
   away before merging" does not remove it.
3. **Public GitHub is scanned continuously by attackers.** Automated scrapers
   watch new public commits for credentials; leaked cloud keys are often abused
   within minutes.

So the only reliable place to stop a secret is **before the commit exists**.
Everything after that point is damage control: rotating the credential, not
recovering it.

A second, smaller problem has the same shape: **large files**. Phase 1 pulls
multi-gigabyte model weights onto the laptop. A weights file committed once
makes every future clone download it, and GitHub refuses any file over 100 MB
at push time, when the only fix is rewriting history.

## Decision

Defense in depth, three layers, each covering what the one before it misses:

| Layer | Where it runs | Tool | Stops |
|---|---|---|---|
| 1. Commit hook | Your laptop, on every `git commit` | pre-commit running gitleaks, a large-file check and Ruff | Secrets and large files before the commit exists |
| 2. CI scan | GitHub Actions, on every PR | gitleaks over every commit in the PR | Anything that skipped layer 1 (`--no-verify`, commits made on github.com, a machine without hooks) |
| 3. Push protection | GitHub's servers, on every push | GitHub secret scanning | Known token formats, even on pushes that bypass both of the above |

Supporting choices:

- **Hook framework:** pre-commit, installed as a dev dependency through uv;
  `make setup` installs the git hook.
- **Secret scanner:** gitleaks (MIT licensed), built by pre-commit from pinned
  source.
- **Large-file guard:** `check-added-large-files` with a 1 MB limit, plus
  `.gitignore` patterns for model weights.
- **Ruff in hooks:** a local hook calling `uv run ruff`, not the
  `ruff-pre-commit` mirror.
- **Pinning:** every remote hook pinned to a full commit SHA.

Layer 2 is implemented in the commit that follows this ADR in the same PR.
Layer 3 is a repository setting that has to be turned on by hand (see
"Consequences").

---

## 1. How the pre-commit framework works

Git runs an executable called `.git/hooks/pre-commit`, if one exists, every
time you run `git commit`. If it exits non-zero, git aborts the commit. That is
the whole mechanism. The catch: `.git/hooks/` is not part of the repository, so
it is not cloned or versioned. Every clone starts with no hooks.

The **pre-commit framework** fills that gap:

- `.pre-commit-config.yaml` (versioned, reviewed in PRs) lists the hooks: which
  repository each comes from, at which version, with which arguments.
- `pre-commit install` writes a small script into `.git/hooks/pre-commit` that
  runs the framework. `make setup` does this, so a fresh clone gets the hooks
  in the same step that installs dependencies.
- For each remote hook, pre-commit clones the hook's repository at the pinned
  version and builds an isolated environment for it in `~/.cache/pre-commit/`:
  a Python virtualenv for Python hooks, a compiled binary for Go hooks. Hooks
  never install anything into the project's `.venv`.
- On each commit, pre-commit passes each hook only the **staged** files that
  match the hook's file types. Hooks that change files (Ruff fixing or
  formatting) cause the commit to stop, so you can review the change and
  re-stage it.

`make setup` runs `pre-commit install --install-hooks`. The extra flag builds
every hook environment immediately instead of on the first commit, so the
one-time cost (about 20 seconds, mostly compiling gitleaks) is paid during
setup rather than in the middle of your first commit.

### Tradeoffs

- **Hooks are advisory.** `git commit --no-verify` skips them, and commits made
  in the GitHub web editor never run them. Layers 2 and 3 exist because of this.
- **Hooks are per clone.** A new clone without `make setup` has no protection
  until someone runs it.
- **pre-commit downloads code.** It fetches hook repositories, and for the Go
  hook a Go toolchain, onto your machine. Pinning hooks to SHAs (section 5)
  limits what can change underneath you; the Go toolchain download itself is
  not pinned by this repository.

### Alternatives

- **Raw git hooks** (a script in `.git/hooks/` or a versioned folder set via
  `core.hooksPath`). No dependency, but you write and maintain the tool
  installation, file filtering and caching yourself.
- **Husky.** The standard in JavaScript projects; requires Node.
- **Lefthook.** A fast single Go binary with parallel execution; smaller
  ecosystem of ready-made hooks.
- **prek.** A Rust reimplementation of pre-commit that reads the same config
  file and is considerably faster. A drop-in swap later if hook speed becomes a
  problem; the pre-commit framework itself is the more established choice today.

---

## 2. gitleaks as the secret scanner

### How detection works

gitleaks is a pattern matcher. Its built-in configuration (222 rules in
v8.30.1) is a list of regular expressions for known credential formats: GitHub
tokens (`ghp_…`), AWS access keys (`AKIA…`), Slack tokens, private key headers,
and so on. Many rules add two refinements:

- **Keywords:** a cheap pre-filter, so the expensive regex only runs on text
  that contains a hint like `ghp_` or `aws`.
- **Entropy:** a randomness score for the matched text. Real secrets are random
  strings with high entropy; `password = "changeme"` is not. A generic
  "api_key = …" rule only fires when the value looks random enough.

The hook runs `gitleaks git --pre-commit --staged --redact --verbose`. That
scans only the diff you are about to commit, which keeps it fast regardless of
repository size. `--redact` replaces the matched secret with `REDACTED` in the
output, so the secret is not echoed into your terminal scrollback (or into a
CI log, in layer 2).

This was tested before merging: in a scratch repository, a commit containing a
made-up `ghp_` token was rejected by the `github-pat` rule with the value
redacted, and the commit was not created.

### Handling false positives

Pattern matching produces false positives, especially test fixtures that look
like credentials. Two escape hatches, both visible in review:

- A `gitleaks:allow` comment on the line exempts that line.
- A `.gitleaksignore` file lists the fingerprints of specific findings to
  ignore.

Both are deliberate, reviewable exceptions. The alternative, turning a rule
off, would silently stop catching real secrets of that type everywhere.

### Tradeoffs

- **Patterns only find what looks like a secret.** A password with no
  recognizable format, split across lines, or base64-encoded can slip through.
  The scanner reduces risk; it does not replace keeping secrets in `.env` and
  out of code to begin with.
- **No verification.** gitleaks cannot tell a live key from a revoked one or a
  fake one. Every finding has to be treated as real until shown otherwise.
- **Built from source by pre-commit.** The hook uses `language: golang`, so the
  first `make setup` compiles gitleaks with a Go toolchain that pre-commit
  downloads itself. No system-wide Go or gitleaks install is needed, at the
  cost of a one-time build.

### Alternatives

- **TruffleHog.** Its distinguishing feature is *verification*: it calls the
  provider's API to check whether a found credential is live. That reduces
  noise, but it means sending the candidate secret to a third-party endpoint
  from wherever the scan runs. It is also AGPL-3.0 licensed, which matters if
  this code is ever embedded in a product. Better suited to auditing existing
  history than to a fast pre-commit check.
- **detect-secrets (Yelp).** Works from a committed baseline file of known
  findings, which suits large legacy codebases with many existing false
  positives. More process than a new repository needs.
- **GitHub push protection alone.** Server-side and impossible to skip with
  `--no-verify`, but it only recognizes token formats from GitHub's partner
  program, and it acts at push time, after the commit already exists locally.
  Used here as layer 3, not as the only layer.
- **The gitleaks Docker image or a system binary** (the `gitleaks-docker` and
  `gitleaks-system` hook variants). Docker would require Docker Desktop to be
  running for every commit; a system binary would need a separate, unpinned
  install on each machine.

---

## 3. Large-file guard

`check-added-large-files` from the standard `pre-commit-hooks` collection
rejects staged files over a size limit. The limit here is 1 MB (the default is
500 KB): large enough for a lockfile or a README chart, far below any model
weights. Files tracked by Git LFS are exempt.

It is paired with `.gitignore` entries for `models/`, `*.gguf` and
`*.safetensors`. The ignore patterns prevent the common case quietly; the hook
catches what the patterns miss (a weights file with an unexpected name or
location).

Tested: in a scratch repository, a 2 MB file was rejected with
"weights.bin (2048 KB) exceeds 1024 KB".

### Tradeoffs

- **Only newly added files are checked.** The hook looks at files git sees as
  *added* in the commit (`git diff --staged --diff-filter=A`). An existing small
  file that later grows past the limit is not caught. The hook's
  `--enforce-all` flag closes that gap at the cost of occasional friction.
- **Legitimate large files** need a raised limit or Git LFS.

---

## 4. Ruff through uv instead of the mirror hook

The usual way to run Ruff in pre-commit is the `astral-sh/ruff-pre-commit`
repository. It carries its own Ruff version pin, separate from the version in
`uv.lock`. When the two drift apart, the hook formats code one way and CI
checks it another, and commits that pass locally fail in CI.

Here Ruff runs as a `local` hook whose command is `uv run --frozen ruff …`.
That runs the exact Ruff version from `uv.lock`, the same one `make lint` and
CI use. `--frozen` uses the lockfile as-is, so a commit never triggers a
dependency re-resolve.

### Tradeoffs

- **Depends on uv being on PATH.** Git GUIs that don't load your shell profile
  (including, sometimes, editor commit buttons) may fail with "uv: not found".
  Committing from a terminal always works.
- **Hooks fix, CI checks.** The hooks run `ruff check --fix` and `ruff format`
  so problems are repaired locally; CI runs the check-only `make lint`, so it
  reports problems rather than silently fixing them in a throwaway checkout.

---

## 5. Pinning hook versions to commit SHAs

Each remote hook's `rev` is a full commit SHA with the version in a comment:

```yaml
rev: 83d9cd684c87d95d656c1458ef04895a7f1cbd8e # v8.30.1
```

The reasoning is the same as for the CI actions in ADR-000: a tag is a movable
pointer that the hook's maintainer, or anyone who compromises their account, can
repoint at different code. A SHA cannot change. This matters more than usual
for a secret scanner, which by design reads every line you commit.

Versions were chosen with the same cooldown rule as the CI actions: the newest
release that had been public for at least two weeks.

### Tradeoffs

SHA pins do not update themselves. `pre-commit autoupdate --freeze` bumps them
to the latest releases as SHAs; doing that on a schedule, with a review, keeps
them current.

---

## 6. Layer 2: scanning every commit in CI

Layer 1 can be skipped, so CI repeats the secret scan on every pull request,
with three differences from the hook:

- **It scans every commit in the PR, not just the final state of the files.**
  A secret added in one commit and deleted in the next leaves the final files
  clean but still lives in the PR's history, which GitHub keeps permanently
  (see Context). The scan covers the range from the PR's base commit to its
  head, which requires checking out full history rather than the usual single
  commit.
- **It always runs with `--redact`.** CI logs on a public repository are
  public. An unredacted finding would publish the very secret the scan caught.
- **The gitleaks binary is downloaded at a pinned version and checked against a
  SHA-256 checksum stored in the workflow file.** The checksum is committed
  here, not fetched from the release page, because a checksum downloaded from
  the same place as the binary would not detect a tampered release.

### Alternatives

- **`gitleaks/gitleaks-action`.** Convenient, but it requires a paid license key
  for organization accounts (free for personal accounts) and adds another
  third-party action to trust and pin. A pinned, checksum-verified binary is a
  few lines of shell and has no licensing dependency if the repository ever
  moves to an organization.
- **Running `pre-commit run --all-files` in CI.** Reuses the same config, but
  it scans files as they are now, not the commit history, so it misses the
  "added then deleted" case that motivates this layer.

---

## 7. Layer 3: GitHub push protection

GitHub's secret scanning runs on GitHub's servers and is free for public
repositories. With push protection on, a push containing a recognized token
format is rejected before it reaches the repository, whether or not local hooks
ran. It recognizes fewer formats than gitleaks (only those from GitHub's
partner program), and a user can bypass a block by giving a reason, but it is
the only layer that cannot be skipped from the laptop.

This is a repository setting, not code: Settings, then Code security, then
enable secret scanning and push protection.

---

## If a secret is committed anyway

The order matters:

1. **Rotate the credential first.** Revoke it at the provider and issue a new
   one. Assume it has already been copied; rewriting history does not
   un-publish it from clones, forks, caches or GitHub's PR references.
2. **Then clean up.** If the commit never left your machine, amend or rewrite
   it locally. If it was pushed, removing it from history is hygiene, not
   containment.
3. **Then fix the cause.** Add the file pattern to `.gitignore`, move the value
   to `.env`, or tighten the scanner configuration, so the same mistake cannot
   recur.

## Consequences

- `make setup` is required after every fresh clone; without it there are no
  local hooks.
- Commits stop when a hook finds a problem or fixes a file. Review the output,
  re-stage, and commit again.
- Test fixtures that look like credentials need a `gitleaks:allow` comment or a
  `.gitleaksignore` entry, both reviewed in the PR.
- By hand, on GitHub: enable secret scanning with push protection, and, once
  the CI scan exists, add its check to the branch ruleset for `main` so a PR
  cannot merge with a finding.

## Revisit when

- **False positives become frequent.** Add a project `.gitleaks.toml` that
  extends the default rules with targeted allowlists rather than disabling
  rules.
- **Custom credential formats appear**, such as this project's own tenant API
  keys in Phase 1. Add a gitleaks rule for their prefix, so the scanner knows
  what your own secrets look like.
- **A file grows past the limit unnoticed.** Turn on `--enforce-all` for the
  large-file hook.
- **Hook speed becomes a problem.** Evaluate prek as a drop-in replacement for
  the pre-commit runner.
- **Auditing all existing history**, for example before making a private repo
  public: run a full-history scan (`gitleaks git` with no range, or TruffleHog
  with verification) once, outside the commit hook.
