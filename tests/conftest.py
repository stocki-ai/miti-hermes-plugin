"""Pytest config: avoid importing plugin package __init__ during collection."""

import sys
from pathlib import Path

# Plugin modules are loaded as top-level scripts by Hermes, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
