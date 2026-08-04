# For pytest runs. The direct `python tests/test_x.py` path does not read this;
# each test carries its own two-line sys.path bootstrap instead.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
