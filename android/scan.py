#!/usr/bin/env python3
"""分析 Sokol 的 Android 头文件依赖，只更新清单，不提取 SDK 材料。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

from extract import ndk_info


def headers(zig, root, source, target, triple, mode):
    """由编译器收集直接和间接包含的头文件，保留原始包含路径。"""
    result = subprocess.run([
        zig, "cc", "-target", target, "-DIMPL", "-DSOKOL_GLES3",
        "-O0" if mode == "debug" else "-O3",
        *(["-DNDEBUG"] if mode != "debug" else []),
        # Zig Safe 为 C 代码开启 FORTIFY，需一并收录其内联头文件。
        *(["-D_FORTIFY_SOURCE=2"] if mode == "safe" else []),
        "-I", str(root / "usr/include"),
        "-I", str(root / "usr/include" / triple),
        "-M", "-MT", "deps", str(source),
    ], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    paths = shlex.split(result.stdout.replace("\\\n", " "))[1:]
    return {Path(os.path.abspath(path)) for path in paths
            if Path(os.path.abspath(path)).is_relative_to(root / "usr/include")}


def main():
    """合并四个 ABI、API 边界及三种编译模式，更新独立头文件清单。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ndk", type=Path, help="Android NDK directory")
    parser.add_argument("sokol", type=Path, help="Sokol src/sokol/c directory")
    parser.add_argument("--zig", default="zig", help="Zig executable")
    parser.add_argument("--output", type=Path, help="Output header manifest")
    args = parser.parse_args()
    ndk = args.ndk.resolve()
    root, version, maximum = ndk_info(ndk)
    sources = sorted(path for path in args.sokol.resolve().glob("*.c")
                     if "imgui" not in path.name)
    if not sources:
        raise ValueError("no Sokol C sources")
    found = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for target, triple in (
            ("aarch64-linux-android", "aarch64-linux-android"),
            ("arm-linux-androideabi", "arm-linux-androideabi"),
            ("x86-linux-android", "i686-linux-android"),
            ("x86_64-linux-android", "x86_64-linux-android"),
        ):
            jobs = [pool.submit(headers, args.zig, root, source,
                                f"{target}.{api}", triple, mode)
                    for api in (26, maximum) for mode in ("debug", "fast", "safe")
                    for source in sources]
            for job in jobs:
                found.update(job.result())
            print(f"collected {triple} header dependencies", flush=True)

    # 清单保存 NDK 版本和原始哈希，普通提取不再依赖 Sokol 或 Zig。
    output = args.output or Path(__file__).resolve().with_name("headers.json")
    output.write_text(json.dumps({
        "ndk_version": version,
        "headers": {
            path.relative_to(root / "usr/include").as_posix():
                hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(found)
        },
    }, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(found)} headers in {output}", flush=True)


if __name__ == "__main__":
    main()
