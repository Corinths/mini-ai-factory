# ADR-001: Secret scanning and commit hooks

- Status: Proposed
- Date: TODO

<!--
Template created by Claude Code; the content is yours to write.
What was implemented: .pre-commit-config.yaml (gitleaks, check-added-large-files,
ruff via uv), `make setup` installing the hook, and model-weight patterns in
.gitignore. A CI secret scan follows in a later commit of the same PR.
-->

## Context

TODO: What risk are you addressing, and why does it have to be handled before a
commit exists rather than after a push?

## Decision

TODO: What did you choose for each of these?

- Hook framework:
- Secret scanner, and how it is installed:
- Large-file guard and its limit:
- How Ruff runs in hooks (and why not the ruff-pre-commit mirror):
- How hook versions are pinned:

## Tradeoffs

TODO: What does each choice cost? Consider: hooks can be skipped with
`--no-verify`, pattern-based scanners miss secrets and raise false positives,
and the first `make setup` builds gitleaks from source.

## Alternatives considered

TODO: What else did you evaluate (for example trufflehog, detect-secrets,
gitleaks via Docker or a system binary, GitHub push protection alone), and why
did you reject it?
