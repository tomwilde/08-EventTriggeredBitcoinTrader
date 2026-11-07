.PHONY: app debug down test acceptance lint type

app:
	docker compose up --build app rabbitmq

debug:
	docker compose --profile debug up --build debug rabbitmq

down:
	docker compose down -v

test:
	uv run pytest tests/ -v

acceptance:
	uv run pytest tests/test_app.py -v

lint:
	uv run ruff check .

type:
	PYTHONPATH=src .venv/bin/ty check
