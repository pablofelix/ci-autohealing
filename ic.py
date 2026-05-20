#!/usr/bin/env python3.11
"""IC CLI entry point — loads .env then delegates to Click command tree."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'collectors', 'python'))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from cli.main import main

main()
