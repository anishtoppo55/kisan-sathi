#!/usr/bin/env bash
# Run the test suite isolated from any system PYTHONPATH (e.g. ROS) and its pytest plugins.
set -euo pipefail
cd "$(dirname "$0")/.."
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -p pytest_asyncio.plugin "$@"
