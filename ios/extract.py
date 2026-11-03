#!/usr/bin/env python3
"""ios SDK 独立材料提取。"""

from pathlib import Path
import sys

# 两个 Apple 平台共用提取逻辑，普通构建不执行 Python。
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apple import main

if __name__ == "__main__":
    main("ios", Path(__file__).resolve().parent)
