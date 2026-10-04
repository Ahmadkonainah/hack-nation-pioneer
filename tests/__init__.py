"""Unit tests for Stackwise. Run from the repository root:  python -m unittest discover -s tests -t .

Only the standard library is needed. No test calls the Claude API or the Census geocoder:
the model client is replaced by a small fake, and the data tests read the files already saved in outputs/.
"""
import sys
from pathlib import Path

# Make `import engine`, `import extract`, ... work the same way they do for the scripts in src/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
