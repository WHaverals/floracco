"""Ensure the project root is importable so tests can `import workflows.*`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
