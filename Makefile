.PHONY: up down infra migrate seed test backend-dev
up:        ## full stack in Docker
	docker compose up --build
down:
	docker compose down
infra:     ## just postgres + redis (+minio if pullable)
	docker compose up -d postgres redis
migrate:
	cd backend && .venv/bin/alembic upgrade head
seed:
	cd backend && .venv/bin/python -m app.seed
test:
	cd backend && .venv/bin/python -m pytest -q
backend-dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
