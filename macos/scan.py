#!/usr/bin/env python3
"""macos SDK 头文件清单更新。"""

from pathlib import Path
import sys

# 两个 Apple 平台共用提取逻辑，普通构建不执行 Python。
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apple import main

if __name__ == "__main__":
    main("macos", Path(__file__).resolve().parent, scanning=True)
