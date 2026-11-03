#!/usr/bin/env python3
"""下载固定版本的开发包，按清单提取 Linux 原始头文件，不生成链接符号。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import posixpath
import shutil
import tarfile
import tempfile
import urllib.request


def download(row, cache):
    """缓存以上游文件名保存，使用固定哈希检查下载内容。"""
    path = cache / row["url"].rsplit("/", 1)[1]
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]:
            return path
        raise ValueError(f"cached package hash mismatch: {path}")
    print(f"Download {path.name}", flush=True)
    with urllib.request.urlopen(row["url"], timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError(f"download hash mismatch: {row['url']}")
    # 完整下载并校验后再落盘，失败不会留下半个缓存文件。
    path.write_bytes(data)
    return path


def archive_data(path):
    """Debian 的 ar 包只读取 data.tar；Alpine 包直接作为 tar 读取。"""
    data = path.read_bytes()
    if path.suffix != ".deb":
        return data
    if data[:8] != b"!<arch>\n":
        raise ValueError(f"invalid Debian archive: {path}")
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset:offset + 60]
        size = int(header[48:58])
        name = header[:16].decode("ascii").strip().rstrip("/")
        if header[58:60] != b"`\n" or offset + 60 + size > len(data):
            raise ValueError(f"invalid Debian member: {path}")
        if name.startswith("data.tar"):
            return data[offset + 60:offset + 60 + size]
        offset += 60 + size + size % 2
    raise ValueError(f"missing data.tar: {path}")


def read_member(archive, members, name, seen):
    """仅在包内解析头文件链接，不向磁盘解包上游路径。"""
    if name in seen:
        raise ValueError(f"archive link loop: {name}")
    seen.add(name)
    member = members[name]
    if member.isfile():
        return archive.extractfile(member).read()
    if member.issym() or member.islnk():
        link = member.linkname
        if member.issym() and not link.startswith("/"):
            link = posixpath.join(posixpath.dirname(name), link)
        link = posixpath.normpath(link).lstrip("/")
        return read_member(archive, members, link, seen)
    raise ValueError(f"not a header file: {name}")


def extract(path, wanted, found, output):
    """只复制清单内文件，逐个校验原始内容，并记录提供该文件的包。"""
    with tarfile.open(fileobj=io.BytesIO(archive_data(path)), mode="r:*",
                      ignore_zeros=True) as archive:
        members = {entry.name.removeprefix("./"): entry for entry in archive}
        for name, digest in wanted.items():
            source = "usr/include/" + name
            if source not in members:
                continue
            data = read_member(archive, members, source, set())
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"header hash mismatch: {path.name}: {name}")
            dest = output / "include" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            found[name] = f"{path.name}: {source} -> include/{name}\n"


def main():
    """头文件选择和版本由 SDK 自己维护，不依赖引擎或 Sokol 工作目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("abi", choices=("gnu", "musl", "all"))
    parser.add_argument("--cache", type=Path, help="Package cache directory")
    parser.add_argument("--output", type=Path, help="Output Linux SDK directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    cache = (args.cache or root / ".cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output = (args.output or root).resolve()
    for abi in (("gnu", "musl") if args.abi == "all" else (args.abi,)):
        source = root / abi
        packages = json.loads((source / "packages.json").read_text("utf-8"))
        wanted = json.loads((source / "headers.json").read_text("utf-8"))
        for name in wanted:
            if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                raise ValueError(f"invalid header path: {name}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            paths = list(pool.map(lambda row: download(row, cache), packages))
        # 所有输入校验成功后才写入目标目录，提取失败保留已有 SDK。
        with tempfile.TemporaryDirectory(prefix="zhuyu-linux-") as folder:
            stage = Path(folder)
            found = {}
            for path in paths:
                extract(path, wanted, found, stage)
            missing = sorted(wanted.keys() - found.keys())
            if missing:
                raise ValueError("missing headers: " + ", ".join(missing))
            dest = output / abi
            existing = {p.relative_to(dest / "include").as_posix()
                        for p in (dest / "include").rglob("*") if p.is_file()}
            extra = sorted(existing - wanted.keys())
            if extra:
                raise ValueError("unlisted output headers: " + ", ".join(extra))
            # 来源与许可随材料一起输出；这些说明本身也保存在 SDK 仓库。
            if dest != source:
                dest.mkdir(parents=True, exist_ok=True)
                for name in ("SOURCE.txt", "packages.json", "headers.json"):
                    shutil.copyfile(source / name, dest / name)
                shutil.copytree(source / "licenses", dest / "licenses",
                                dirs_exist_ok=True)
            shutil.copytree(stage / "include", dest / "include",
                            dirs_exist_ok=True)
            (dest / "FILES.txt").write_text(
                "".join(found[name] for name in sorted(found)), encoding="utf-8")
            print(f"{abi}: extracted and verified {len(found)} headers", flush=True)


if __name__ == "__main__":
    main()
