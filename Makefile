.PHONY: demo check e2e container-smoke verify cms

cms:
	uv run python scripts/bootstrap_cms_grouper.py

demo: cms
	uv run uvicorn app.api.main:app --host 127.0.0.1 --port 8000

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app
	uv run lint-imports
	uv run pytest -q

e2e: cms
	npm run test:e2e

container-smoke:
	./scripts/container_smoke.sh mds-recapture-workbench

verify: check e2e
	docker build --no-cache -t mds-recapture-workbench .
	$(MAKE) container-smoke
