.PHONY: help sync lock

help:
	@echo "Available commands:"
	@echo "  sync  - Run the sync_stats.py script using uv"
	@echo "  lock  - Update the uv lockfile"

sync:
	UV_CACHE_DIR=./.uv_cache uv run python -m src.sync_stats

lock:
	uv lock
