# Shared command interface: you, Claude Code and CI all run these same targets,
# so "passes locally" and "passes in CI" mean the same thing.
#
# Every command goes through `uv run`, which first brings .venv in line with
# uv.lock. There is no virtualenv to activate and nothing to reinstall after a
# pull. Recipe lines must be indented with a tab, not spaces.

# These targets are commands, not files. Without .PHONY, a file named `test`
# in the repo root would make `make test` report "up to date" and do nothing.
.PHONY: setup lint format test

# Install all workspace members and dev tools into .venv.
# TODO: install pre-commit hooks (ruff, gitleaks) once that PR lands.
setup:
	uv sync

# Check-only: reports problems without changing files. This is what CI runs.
lint:
	uv run ruff check .
	uv run ruff format --check .

# Local counterpart to `lint`: applies safe autofixes and formatting in place.
format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
