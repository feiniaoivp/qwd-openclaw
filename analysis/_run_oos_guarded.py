#!/usr/bin/env python3
import os
import sys
import socket

socket.setdefaulttimeout(15)

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

import runpy
runpy.run_path(os.path.join(WORKSPACE, "analysis", "oos_validate_grid.py"), run_name="__main__")
