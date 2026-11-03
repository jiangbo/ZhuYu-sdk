#!/usr/bin/env python3
"""独立整理 Apple SDK；按清单提取与扫描头文件依赖分开执行。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def header_path(relative):
    """将 Framework 头文件展开为普通 include 路径，保留框架名。"""
    if relative.startswith("usr/include/"):
        return relative
    match = re.search(
        r"/([^/]+)\.framework/(?:Versions/[^/]+/)?Headers/(.*)$", "/" + relative)
    if match is None:
        raise ValueError(f"unsupported SDK header path: {relative}")
    return f"usr/include/{match[1]}/{match[2]}"


def scan_headers(zig, sdk, source, target, fast):
    """仅让编译器分析头文件依赖，不编译目标文件或分析链接符号。"""
    result = subprocess.run([
        zig, "cc", "-target", target, "-x", "objective-c",
        "-DIMPL", "-DSOKOL_METAL", "-O3" if fast else "-O0",
        *(["-DNDEBUG"] if fast else []),
        "-Wno-deprecated-declarations", "-Wno-macro-redefined",
        "-I", str(sdk / "usr/include"),
        "-iframework", str(sdk / "System/Library/Frameworks"),
        "-M", "-MT", "deps", str(source),
    ], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    paths = shlex.split(result.stdout.replace("\\\n", " "))[1:]
    # 不 resolve，保留 SDK 符号链接入口的名称；提取时写成普通文件。
    paths = {Path(os.path.abspath(path)) for path in paths}
    return {path for path in paths if path.is_relative_to(sdk)}


def scan(zig, sdk, sources, targets, output):
    """扫描只更新头文件清单，不依赖引擎源码，也不写入 SDK 材料。"""
    settings = json.loads((sdk / "SDKSettings.json").read_text("utf-8"))
    found = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(scan_headers, zig, sdk, source, target, fast)
                for target in targets for fast in (False, True) for source in sources]
        for job in jobs:
            found.update(job.result())
    manifest = {
        "sdk_version": settings["Version"],
        "sdk_name": settings["CanonicalName"],
        "headers": {
            path.relative_to(sdk).as_posix(): {
                "path": header_path(path.relative_to(sdk).as_posix()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            } for path in sorted(found)
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(found)} headers in {output}", flush=True)


def extract(sdk, source, output, targets):
    """按固定清单提取 SDK，不读取 Sokol、ZhuYu，也不生成精简 TBD。"""
    if output == sdk or output.is_relative_to(sdk):
        raise ValueError("output must be outside the input SDK")
    manifest = json.loads((source / "headers.json").read_text("utf-8"))
    settings = json.loads((sdk / "SDKSettings.json").read_text("utf-8"))
    if (settings["Version"] != manifest["sdk_version"] or
            settings["CanonicalName"] != manifest["sdk_name"]):
        raise ValueError(f"expected SDK {manifest['sdk_name']}")
    with tempfile.TemporaryDirectory(prefix="zhuyu-apple-sdk-") as folder:
        stage = Path(folder)
        records = []
        for name, entry in manifest["headers"].items():
            dest = entry["path"]
            for path in (name, dest):
                if (path.startswith("/") or "\\" in path or
                        ".." in path.split("/")):
                    raise ValueError(f"invalid SDK path: {path}")
            if dest != header_path(name):
                raise ValueError(f"invalid header output: {dest}")
            data = (sdk / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"header hash mismatch: {name}")
            path = stage / dest
            if path.exists() and path.read_bytes() != data:
                raise ValueError(f"conflicting SDK headers: {dest}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            records.append(f"{name} -> {dest}\n")
        (stage / "usr/lib").mkdir(parents=True, exist_ok=True)
        system = sdk / "usr/lib/libSystem.tbd"
        if any(target.endswith("-ios-simulator") for target in targets):
            # 模拟器展开完整的宿主桥接接口，不按引擎实际调用裁剪。
            from apple_tbd import exports, library_groups, write_library

            required = {}
            for target in targets:
                names = set()
                for group in library_groups(sdk, system, target):
                    names.update(exports(group))
                required[target] = names
            write_library(sdk, system, stage / "usr/lib/libSystem.tbd", required)
        else:
            shutil.copyfile(system, stage / "usr/lib/libSystem.tbd")
        records.append("usr/lib/libSystem.tbd -> usr/lib/libSystem.tbd\n")
        if settings["MinimalDisplayName"].startswith("Simulator - "):
            settings["MinimalDisplayName"] = settings["Version"]
        (stage / "SDKSettings.json").write_text(
            json.dumps(settings) + "\n", encoding="utf-8")
        records.append("SDKSettings.json -> SDKSettings.json\n")
        (stage / "FILES.txt").write_text("".join(records), encoding="utf-8")
        shutil.copyfile(source / "headers.json", stage / "headers.json")
        # 先完成校验，清单外的旧材料交给维护者核对，不直接删除。
        existing = {p.relative_to(output).as_posix()
                    for p in (output / "usr").rglob("*") if p.is_file()}
        expected = {p.relative_to(stage).as_posix()
                    for p in (stage / "usr").rglob("*") if p.is_file()}
        extra = sorted(existing - expected)
        if extra:
            raise ValueError("unlisted output files: " + ", ".join(extra))
        if (source / "SOURCE.txt").is_file():
            shutil.copyfile(source / "SOURCE.txt", stage / "SOURCE.txt")
        shutil.copytree(stage, output, dirs_exist_ok=True)
    print(f"{output.name}: extracted {len(manifest['headers'])} header sources "
          "and complete libSystem interfaces", flush=True)


def main(platform, source, scanning=False):
    """平台入口只指定原始 SDK 和输出位置，两套流程分别解析参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path, help="Original Apple SDK directory")
    if platform == "ios":
        parser.add_argument("simulator", type=Path, help="Original iOS Simulator SDK")
    if scanning:
        parser.add_argument("sokol", type=Path, help="Sokol src/sokol/c directory")
        parser.add_argument("--zig", default="zig", help="Zig executable")
    parser.add_argument("--output", type=Path, help="Output platform directory")
    args = parser.parse_args()
    output = (args.output or source).resolve()
    if scanning:
        sources = sorted(path for path in args.sokol.resolve().glob("*.c")
                         if "imgui" not in path.name)
        if not sources:
            raise ValueError("no Sokol C sources")
    if platform == "macos":
        jobs = ((args.sdk, source, output,
                 ("aarch64-macos.14.0", "x86_64-macos.14.0"),
                 ("arm64-macos", "x86_64-macos")),)
    else:
        jobs = (
            (args.sdk, source / "device", output / "device",
             ("aarch64-ios.15.0",), ("arm64-ios",)),
            (args.simulator, source / "simulator", output / "simulator",
             ("aarch64-ios.15.0-simulator", "x86_64-ios.15.0-simulator"),
             ("arm64-ios-simulator", "x86_64-ios-simulator")),
        )
    for sdk, material, dest, targets, libraries in jobs:
        if scanning:
            scan(args.zig, sdk.resolve(), sources, targets, dest / "headers.json")
        else:
            extract(sdk.resolve(), material, dest, libraries)
