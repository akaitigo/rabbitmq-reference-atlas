#!/usr/bin/env python3
"""RabbitMQ Depth Parity generatorの安定した実行Entry。"""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).with_name("generate-fe-parity-matrix.py")), run_name="__main__")
