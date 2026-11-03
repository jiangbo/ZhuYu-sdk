#!/usr/bin/env python3
"""分析 Sokol 的 Windows x86、x64 头文件依赖，只更新原始文件清单。"""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    """合并两种架构、三种模式的依赖，保留包含路径中的大小写。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path, help="xwin splat directory")
    parser.add_argument("sokol", type=Path, help="Sokol src/sokol/c directory")
    parser.add_argument("--zig", default="zig", help="Zig executable")
    parser.add_argument("--output", type=Path, help="Output header manifest")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    sources = sorted(path for path in args.sokol.resolve().glob("*.c")
                     if "imgui" not in path.name)
    if not sources:
        raise ValueError("no Sokol C sources")
    found = set()
    for arch in ("x86", "x86_64"):
        for mode in ("debug", "safe", "fast"):
            for source in sources:
                command = [
                    args.zig, "cc", "-target", f"{arch}-windows.win8-msvc",
                    "-DIMPL", "-DSOKOL_D3D11",
                    "-O0" if mode == "debug" else "-O3",
                    *(["-DNDEBUG"] if mode != "debug" else []),
                    "-M", "-MT", "deps",
                ]
                for name in ("crt/include", "sdk/include/ucrt", "sdk/include/um",
                             "sdk/include/shared"):
                    command += ["-isystem", str(sdk / name)]
                result = subprocess.run([*command, str(source)],
                                        capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError(result.stderr)
                for name in shlex.split(result.stdout.replace("\\\n", " "))[1:]:
                    # 不解析软链接，避免丢失头文件使用的路径大小写。
                    path = Path(name).absolute()
                    if path.is_relative_to(sdk):
                        found.add(path.relative_to(sdk).as_posix())
            print(f"collected {arch} {mode} header dependencies", flush=True)
    if not found:
        raise ValueError("no Microsoft SDK headers found")
    output = args.output or Path(__file__).resolve().with_name("headers.json")
    output.write_text(json.dumps({
        "targets": ["x86-windows.win8-msvc", "x86_64-windows.win8-msvc"],
        "headers": {name: hashlib.sha256((sdk / name).read_bytes()).hexdigest()
                    for name in sorted(found)},
    }, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(found)} headers in {output}", flush=True)


if __name__ == "__main__":
    main()
