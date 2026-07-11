"""Make the collector scripts importable as top-level modules (they live flat
in collector/, not in a package — same layout the CI smoke-import relies on)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
