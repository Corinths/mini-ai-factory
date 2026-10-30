.PHONY: setup lint format test

# Install all workspace members and dev tools into .venv.
# TODO: install pre-commit hooks (ruff, gitleaks) once that PR lands.
setup:
	uv sync

# Lint and check formatting without changing files (what CI runs).
lint:
	uv run ruff check .
	uv run ruff format --check .

# Apply lint autofixes and formatting.
format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
