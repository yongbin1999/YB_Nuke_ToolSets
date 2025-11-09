# -*- coding: utf-8 -*-
"""
YB Tools - Plugin Initialization
"""

import nuke
import os
import sys

plugin_path = os.path.dirname(__file__)

# Add icon path (yb_logo.png in root directory)
nuke.pluginAddPath(plugin_path)

# Add AEBridge module path (so Python can import AEBridge module)
nuke.pluginAddPath(os.path.join(plugin_path, 'AEBridge'))

# Add plugin root directory to Python path (so updater module can be imported)
if plugin_path not in sys.path:
    sys.path.insert(0, plugin_path)

