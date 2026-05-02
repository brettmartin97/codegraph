.PHONY: install test lint typecheck ci docker-build docker-up clean e2e

install:
	pip install -e ".[dev,mcp]"

test:
	pytest tests/ -v --tb=short -k "not e2e"

e2e:
	pytest tests/ -v --tb=short --timeout=300 -k "e2e"

test-all:
	pytest tests/ -v --tb=short --timeout=300

test-cov:
	pytest tests/ --cov=codegraph_mcp --cov-report=term-missing

lint:
	ruff check src/ tests/

lint-fix:
	ruff check --fix src/ tests/

typecheck:
	mypy src/codegraph_mcp/

ci: lint test

docker-build:
	docker build -t codegraph-mcp:dev .

docker-start:
	@echo "Usage: powershell scripts/start-mcp.ps1 -RepoPath <path>"
	@echo "Starts the container with the given repo mounted at /repo."
	@echo "Then call register_and_index(path='/repo') from Copilot Chat."

docker-up:
	docker compose up --build

docker-index:
	docker compose run --rm codegraph-mcp codegraph repo add sample /repos/sample
	docker compose run --rm codegraph-mcp codegraph index sample

smoke:
	codegraph doctor
	codegraph repo list

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	python -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('.pytest_cache')]"
	python -c "import pathlib; [p.unlink() for p in pathlib.Path('.').rglob('*.pyc') if p.is_file()]"
	python -c "import pathlib; p=pathlib.Path('data/codegraph.db'); p.exists() and p.unlink()"

help:
	@echo "Available targets:"
	@echo "  install      Install package in editable mode with dev dependencies"
	@echo "  test         Run unit test suite (excludes e2e)"
	@echo "  test-all     Run all tests including e2e"
	@echo "  e2e          Run e2e / integration tests"
	@echo "  test-cov     Run tests with coverage report"
	@echo "  lint         Run ruff linter"
	@echo "  lint-fix     Auto-fix ruff issues"
	@echo "  typecheck    Run mypy type checker"
	@echo "  ci           Run lint + tests (for CI)"
	@echo "  docker-build Build Docker image"
	@echo "  docker-up    Start via Docker Compose"
	@echo "  docker-index Index sample repo in Docker"
	@echo "  smoke        Quick smoke test of CLI"
	@echo "  clean        Remove build artifacts and local DB"
