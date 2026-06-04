"""Test bootstrap: put the plugin dir on sys.path so the *_core modules import
standalone (their relative ``from . import cluster_ops_client`` falls back to a
flat import — see the try/except at the top of disk_core/service_core)."""

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)
