#!/usr/bin/env python3
"""按清单提取 Windows x86、x64、ARM64 SDK，并移除静态库调试信息。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def clear_hybrid(source, output):
    """原位清空混合库的调试节，保留成员位置和 ARM64EC 符号索引。"""
    data = bytearray(source.read_bytes())
    if data[:8] != b"!<arch>\n":
        raise ValueError("invalid COFF archive")
    pos = 8
    while pos < len(data):
        header = data[pos:pos + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValueError("invalid archive member")
        size = int(header[48:58])
        start = pos + 60
        end = start + size
        if end > len(data):
            raise ValueError("invalid archive member size")
        name = header[:16].strip()
        # 索引和长名称表原样保留，只处理普通对象成员。
        if name not in (b"/", b"//", b"/<ECSYMBOLS>/"):
            obj = data[start:end]
            if (obj[:4] == b"\x00\x00\xff\xff"
                    and int.from_bytes(obj[4:6], "little") >= 2):
                offset = start + 56
                count = int.from_bytes(obj[44:48], "little")
            elif obj[:2] in (b"\x64\xaa", b"\x41\xa6", b"\x64\x86"):
                offset = start + 20 + int.from_bytes(obj[16:18], "little")
                count = int.from_bytes(obj[2:4], "little")
            else:
                offset, count = start, 0
            for part in range(count):
                section = offset + part * 40
                if section + 40 > end:
                    raise ValueError("invalid COFF section header")
                header = data[section:section + 40]
                if header[:8].rstrip(b"\x00") not in (b".debug$S", b".debug$T"):
                    continue
                flags = int.from_bytes(header[36:40], "little")
                length = int.from_bytes(header[16:20], "little")
                pointer = int.from_bytes(header[20:24], "little")
                reloc = int.from_bytes(header[24:28], "little")
                number = int.from_bytes(header[32:34], "little")
                if flags & 0x01000000:
                    number = int.from_bytes(obj[reloc:reloc + 4], "little")
                # 清空无用字节，归档压缩后不再为调试数据占空间。
                for point, length in ((pointer, length), (reloc, number * 10)):
                    if length:
                        if point + length > size:
                            raise ValueError("invalid debug section data")
                        data[start + point:start + point + length] = bytes(length)
                data[section + 8:section + 36] = bytes(28)
                data[section + 36:section + 40] = (
                    flags & ~0x01000000).to_bytes(4, "little")
        pos = end + (size % 2)
    output.write_bytes(data)


def clear_debug(source, output, objcopy):
    """x86 清空调试节内容，保留 SafeSEH 引用的原始符号索引。"""
    # LLVM 19 直接删节会弄乱 .sxdata；保留节和符号，只清空调试内容。
    tool = Path(shutil.which(objcopy) or objcopy).absolute()
    members = subprocess.check_output(
        [str(tool.with_name("llvm-ar")), "t", str(source)], text=True).splitlines()
    with tempfile.TemporaryDirectory(prefix="zhuyu-coff-") as folder:
        stage = Path(folder)
        objects = []
        for index, name in enumerate(members):
            data = subprocess.check_output(
                [str(tool.with_name("llvm-ar")), "pP", str(source), name])
            # 普通 COFF 和 BigObj 的节表位置不同；导入对象无须处理。
            if data[:2] in (b"\x4c\x01", b"\x00\x00") and data[2:4] != b"\xff\xff":
                offset = 20 + int.from_bytes(data[16:18], "little")
                count = int.from_bytes(data[2:4], "little")
            elif (data[:4] == b"\x00\x00\xff\xff"
                  and int.from_bytes(data[4:6], "little") >= 2):
                offset = 56
                count = int.from_bytes(data[44:48], "little")
            else:
                offset, count = 0, 0
            data = bytearray(data)
            for part in range(count):
                start = offset + part * 40
                header = data[start:start + 40]
                if len(header) != 40:
                    raise ValueError("invalid COFF section header")
                name = header[:8].rstrip(b"\x00")
                if name in (b".debug$S", b".debug$T"):
                    # 调试节的数据、重定位和行号引用同时清空。
                    data[start + 8:start + 36] = bytes(28)
                    flags = int.from_bytes(header[36:40], "little") & ~0x01000000
                    data[start + 36:start + 40] = flags.to_bytes(4, "little")
            original = stage / "input.obj"
            original.write_bytes(data)
            dest = stage / f"{index:04}.obj"
            # objcopy 重排文件布局，去掉空洞；节与符号顺序保持不变。
            subprocess.run([objcopy, str(original), str(dest)], check=True)
            objects.append(dest.name)
        # 固定对象名称和顺序，不把临时目录写进新库。
        response = stage / "objects.rsp"
        response.write_text("\n".join(objects), encoding="utf-8")
        subprocess.run([str(tool.with_name("llvm-ar")), "--format=coff", "rcsD",
                        "result.lib", f"@{response}"], cwd=stage, check=True)
        shutil.copyfile(stage / "result.lib", output)


def main():
    """校验原始和处理后的哈希，全部成功后才更新材料目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path, help="xwin splat directory")
    parser.add_argument("--objcopy", default="llvm-objcopy",
                        help="LLVM objcopy executable")
    parser.add_argument("--output", type=Path, help="Output Windows SDK directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sdk = args.sdk.resolve()
    output = (args.output or root).resolve()
    if output == sdk or output.is_relative_to(sdk):
        raise ValueError("output must be outside the input SDK")
    headers = json.loads((root / "headers.json").read_text("utf-8"))["headers"]
    libraries = json.loads((root / "libraries.json").read_text("utf-8"))
    if set(headers) & set(libraries):
        raise ValueError("header and library paths overlap")
    with tempfile.TemporaryDirectory(prefix="zhuyu-windows-") as folder:
        stage = Path(folder)
        records = []
        for name in sorted(headers.keys() | libraries.keys()):
            if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                raise ValueError(f"invalid SDK path: {name}")
            data = (sdk / name).read_bytes()
            library = libraries.get(name)
            digest = library["source_sha256"] if library else headers[name]
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"source hash mismatch: {name}")
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if library and library["action"] != "copy":
                if library["action"] == "clear-hybrid":
                    clear_hybrid(sdk / name, dest)
                elif library["action"] == "clear":
                    clear_debug(sdk / name, dest, args.objcopy)
                elif library["action"] == "strip":
                    subprocess.run([args.objcopy, "--strip-debug",
                                    str(sdk / name), str(dest)], check=True)
                else:
                    raise ValueError(f"invalid library action: {name}")
                if hashlib.sha256(dest.read_bytes()).hexdigest() != library["sha256"]:
                    raise ValueError(f"stripped hash mismatch: {name}")
                records.append(f"{name} -> {name} [{library['action']}-debug]")
            else:
                # 普通文件直接写入，xwin 的大小写软链接也转为普通文件。
                dest.write_bytes(data)
                records.append(f"{name} -> {name} [copy]")
        wanted = headers.keys() | libraries.keys()
        existing = {path.relative_to(output).as_posix()
                    for part in ("crt", "sdk")
                    for path in (output / part).rglob("*") if path.is_file()}
        extra = sorted(existing - wanted)
        if extra:
            raise ValueError("unlisted output files: " + ", ".join(extra))
        for name in ("SOURCE.txt", "NOTICE.txt", "headers.json", "libraries.json"):
            shutil.copyfile(root / name, stage / name)
        (stage / "FILES.txt").write_text("\n".join(records) + "\n",
                                        encoding="utf-8")
        shutil.copytree(stage, output, dirs_exist_ok=True)
        print(f"extracted {len(headers)} headers and {len(libraries)} libraries "
              f"to {output}", flush=True)


if __name__ == "__main__":
    main()
