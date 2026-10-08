#!/usr/bin/env bash
set -euo pipefail
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m evaluation.run --offline
