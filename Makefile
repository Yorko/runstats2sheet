.PHONY: help sync lock test

help:
	@echo "Available commands:"
	@echo "  sync  - Run the sync_stats.py script using uv"
	@echo "  test  - Run the test suite with pytest"
	@echo "  lock  - Update the uv lockfile"

sync:
	UV_CACHE_DIR=./.uv_cache uv run python -m src.sync_stats

test:
	UV_CACHE_DIR=./.uv_cache uv run pytest

lock:
	uv lock