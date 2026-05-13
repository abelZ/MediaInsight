#!/usr/bin/env python3
"""Launch script for Media Analyzer."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from media_analyzer.__main__ import main

if __name__ == "__main__":
    main()
