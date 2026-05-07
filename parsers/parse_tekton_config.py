#!/usr/bin/env python3
"""Extract path-context from Tekton task JSON config."""

import sys
import json
import yaml

try:
    data = json.load(sys.stdin)
    if data:
        config = yaml.safe_load(data)
        for param in config.get('spec', {}).get('params', []):
            if param.get('name') == 'path-context':
                print(param.get('value', ''))
                break
except Exception:
    pass
