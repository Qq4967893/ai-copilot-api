.PHONY: install run test lint fmt migrate revision

install:
	uv sync

run:
	uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 9092

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

fmt:
	uv run ruff format .
	uv run ruff check --fix .

migrate:
	uv run alembic upgrade head

# make revision m="add xxx"
revision:
	uv run alembic revision --autogenerate -m "$(m)"
