#!/usr/bin/env python3
"""按独立清单从本地 NDK 提取 Android 材料，只需要 Python。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def ndk_info(ndk):
    """读取本地 NDK 的 sysroot、版本和 API 上限，不依赖引擎目录。"""
    roots = sorted((ndk / "toolchains/llvm/prebuilt").glob("*/sysroot"))
    if len(roots) != 1:
        raise ValueError("expected one NDK host sysroot")
    properties = dict(tuple(part.strip() for part in line.split("=", 1))
                      for line in
                      (ndk / "source.properties").read_text("utf-8").splitlines()
                      if "=" in line)
    version = properties["Pkg.Revision"]
    maximum = json.loads((ndk / "meta/platforms.json").read_text("utf-8"))["max"]
    if maximum < 26:
        raise ValueError("NDK must support API 26 or later")
    return roots[0], version, maximum


def main():
    """先完整提取并校验，再写入目标目录，失败时保留已有材料。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ndk", type=Path, help="Android NDK directory")
    parser.add_argument("--output", type=Path, help="Output Android SDK directory")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    output = (args.output or source).resolve()
    ndk = args.ndk.resolve()
    if output == ndk or output.is_relative_to(ndk):
        raise ValueError("output must be outside the input NDK")
    root, version, maximum = ndk_info(ndk)
    manifest = json.loads((source / "headers.json").read_text("utf-8"))
    if version != manifest["ndk_version"]:
        raise ValueError(f"expected NDK {manifest['ndk_version']}, got {version}")
    with tempfile.TemporaryDirectory(prefix="zhuyu-android-") as folder:
        stage = Path(folder)
        records = []
        for name, digest in manifest["headers"].items():
            if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                raise ValueError(f"invalid header path: {name}")
            relative = Path("usr/include") / name
            data = (root / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"header hash mismatch: {relative}")
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            records.append(relative.as_posix())
        # 库与 CRT 直接复制 NDK 原始文件，保留 SONAME 和符号版本。
        for triple in (
            "aarch64-linux-android", "arm-linux-androideabi",
            "i686-linux-android", "x86_64-linux-android",
        ):
            for api in range(26, maximum + 1):
                libraries = root / "usr/lib" / triple / str(api)
                if not libraries.is_dir():
                    continue
                paths = list(libraries.glob("crt*.o"))
                paths.extend(libraries / f"lib{name}.so" for name in (
                    "c", "dl", "m", "android", "log", "aaudio", "EGL", "GLESv3",
                ))
                for path in sorted(paths):
                    relative = path.relative_to(root)
                    dest = stage / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
                    records.append(relative.as_posix())
        # 清单外的已有文件需先人工核对，避免版本切换后混入旧材料。
        existing = {p.relative_to(output).as_posix()
                    for p in (output / "usr").rglob("*") if p.is_file()}
        extra = sorted(existing - set(records))
        if extra:
            raise ValueError("unlisted output files: " + ", ".join(extra))
        shutil.copyfile(root / "NOTICE", stage / "NOTICE")
        shutil.copyfile(ndk / "source.properties", stage / "source.properties")
        shutil.copyfile(source / "headers.json", stage / "headers.json")
        shutil.copyfile(source / "SOURCE.txt", stage / "SOURCE.txt")
        (stage / "FILES.txt").write_text(
            "\n".join(sorted(records)) + "\n", encoding="utf-8")
        shutil.copytree(stage, output, dirs_exist_ok=True)
        print(f"extracted {len(manifest['headers'])} headers and "
              f"API 26-{maximum} libraries to {output}", flush=True)


if __name__ == "__main__":
    main()
