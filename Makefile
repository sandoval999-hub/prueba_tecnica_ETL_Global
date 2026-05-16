# ===========================================================
# Makefile — Atlas RA Seismic ETL Pipeline
# Convenience commands for development and operations
# ===========================================================

.PHONY: help daily alert historical test test-cov docker-up docker-down \
        db-init health lint clean dry-run

# Default target
help: ## Show this help
	@echo "Atlas RA Seismic ETL Pipeline — Available Commands"
	@echo "=================================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

# ── Pipeline execution ────────────────────────────────────

daily: ## Run pipeline in daily mode (last 24h, all regions, mag ≥ 1.0)
	python main.py --mode daily

dagster: ## Start the Dagster UI (Phase 2 Orchestration)
	dagster dev -m src.orchestration -p 3000

alert: ## Run pipeline in alert mode (last 1h, global, mag ≥ 4.5)
	python main.py --mode alert

historical: ## Run historical backfill (requires START and END env vars)
	python main.py --mode historical --start-date $(START) --end-date $(END)

dry-run: ## Run daily mode without loading to MySQL
	python main.py --mode daily --dry-run --verbose

# ── Testing ────────────────────────────────────────────────

test: ## Run unit tests
	pytest tests/ -v -m unit

test-cov: ## Run tests with coverage report
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=html

test-all: ## Run all tests (unit + integration)
	pytest tests/ -v

# ── Docker ─────────────────────────────────────────────────

docker-up: ## Start MySQL + pipeline with Docker Compose
	docker-compose up -d

docker-down: ## Stop all Docker containers
	docker-compose down

docker-logs: ## Tail pipeline container logs
	docker-compose logs -f pipeline

docker-mysql: ## Open MySQL shell in the container
	docker exec -it mysql-seismic mysql -u root -patlas2025 seismic_db

# ── Database ───────────────────────────────────────────────

db-init: ## Initialize database schema (run migrations)
	mysql -h 127.0.0.1 -u root -patlas2025 < sql/init_db.sql

# ── Operations ─────────────────────────────────────────────

health: ## Run health check
	python health_check.py --verbose

reset-cb: ## Reset the circuit breaker to CLOSED
	python health_check.py --reset-circuit-breaker

# ── Code quality ───────────────────────────────────────────

lint: ## Run linting checks (requires ruff)
	ruff check src/ tests/ main.py health_check.py

format: ## Auto-format code (requires ruff)
	ruff format src/ tests/ main.py health_check.py

# ── Cleanup ────────────────────────────────────────────────

clean: ## Remove runtime artifacts (logs, output, caches)
	rm -rf logs/*.log logs/*.log.* logs/circuit_breaker_state.json
	rm -rf output/*.md output/charts/*.png
	rm -rf __pycache__ src/__pycache__ tests/__pycache__
	rm -rf .pytest_cache .coverage htmlcov
	@echo "Cleaned."
