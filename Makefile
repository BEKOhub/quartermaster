.PHONY: help setup install test run up down logs seed clean fmt

help:
	@echo "Quartermaster — common tasks"
	@echo "  make setup     copy example.env -> .env (if missing)"
	@echo "  make install   pip install dev deps into the current venv"
	@echo "  make test      run the pytest suite"
	@echo "  make up        docker compose up --build (full stack)"
	@echo "  make down      docker compose down"
	@echo "  make logs      tail the agent container logs"
	@echo "  make seed      enqueue a demo ticket (mock mode)"
	@echo "  make evals     run the pipeline eval scorecard (regression + red-team)"
	@echo "  make scale     run distributed profile (1 poller + 3 workers + dashboard)"
	@echo "  make run       run the agent locally (no docker; needs redis running)"
	@echo "  make clean     remove local state (data/, worktrees/)"

setup:
	@test -f .env || (cp example.env .env && echo "created .env from example.env")

install:
	pip install -e ".[dev]"

test:
	pytest

evals:
	python -m quartermaster.evals

scale:
	docker compose --profile distributed up --build --scale worker=3

up: setup
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f agent

seed:
	docker compose exec agent python -m quartermaster.seed

run:
	python -m quartermaster.main

clean:
	rm -rf data worktrees *.db audit
