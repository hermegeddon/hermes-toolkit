"""Test bootstrap: put the plugin dir on sys.path so ``weather_core`` imports
standalone (the tests import it flat, mirroring intent-handlers-core's tests).

The tests are network-free: every test either exercises the pure matcher/parser
functions or monkeypatches the HTTP layer. No Open-Meteo call is ever made.
"""

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)
