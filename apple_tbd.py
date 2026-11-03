"""解析和展开 SDK 的 TBD；只在整理链接描述时需要 PyYAML。"""

from functools import cache
import json
import re
import textwrap

import yaml


def exports(group):
    """将 TBD 的 Objective-C 分类展开成实际链接符号，用于覆盖检查。"""
    result = set(group.get("symbols", []))
    result.update(group.get("weak-def-symbols", []))
    result.update(group.get("thread-local-symbols", []))
    for name in group.get("objc-classes", []):
        result.add(f"_OBJC_CLASS_$_{name}")
        result.add(f"_OBJC_METACLASS_$_{name}")
    result.update(f"_OBJC_IVAR_$_{name}" for name in group.get("objc-ivars", []))
    result.update(f"_OBJC_EHTYPE_$_{name}" for name in group.get("objc-eh-types", []))
    return result


@cache
def read_library(path):
    """缓存原始 TBD，避免多个框架和架构重复解析同一份内容。"""
    return list(yaml.load_all(path.read_text(), Loader=yaml.BaseLoader))


def library_path(sdk, name):
    """将真实加载路径映射到 SDK 的 TBD，兼容展开后的 Framework 目录。"""
    relative = name.lstrip("/").removesuffix(".dylib")
    for relative in (relative, re.sub(r"/Versions/[^/]+/", "/", relative)):
        path = sdk / f"{relative}.tbd"
        if path.is_file():
            return path
    raise FileNotFoundError(f"missing reexported library in {sdk}: {name}")


def library_groups(sdk, source, target):
    """按真实重导出关系展开内嵌及外部 TBD，保持父框架的符号可见性。"""
    docs = read_library(source)
    libraries = {doc["install-name"]: doc for doc in docs}
    targets = {target}
    # 模拟器的系统接口经桥接库重导出到宿主 macOS，保留同架构接口。
    if docs[0]["install-name"] == "/usr/lib/libSystem.B.dylib" and \
            target.endswith("-ios-simulator"):
        targets.add(target.replace("-ios-simulator", "-macos"))
    pending = [docs[0]]
    seen = set()
    while pending:
        doc = pending.pop()
        if doc["install-name"] in seen:
            continue
        seen.add(doc["install-name"])
        for group in doc.get("exports", []) + doc.get("reexports", []):
            if targets.intersection(group["targets"]):
                yield group
        for group in doc.get("reexported-libraries", []):
            if targets.intersection(group["targets"]):
                for name in group["libraries"]:
                    if name not in libraries:
                        for child in read_library(library_path(sdk, name)):
                            libraries.setdefault(child["install-name"], child)
                    pending.append(libraries[name])


def write_library(sdk, source, dest, required):
    """保留真实加载路径和版本，按架构筛选导出，不引入无关重导出依赖。"""
    docs = read_library(source)
    data = {"tbd-version": 4, "targets": list(required)}
    for key in ("install-name", "current-version", "compatibility-version"):
        if key in docs[0]:
            data[key] = docs[0][key]
    groups = []
    provided = {target: set() for target in required}
    for target, names in required.items():
        selected = {}
        for group in library_groups(sdk, source, target):
            for key, values in group.items():
                if key == "targets":
                    continue
                for value in values:
                    if exports({key: [value]}) & names:
                        selected.setdefault(key, set()).add(value)
        group = {"targets": [target]}
        group.update({key: sorted(values) for key, values in selected.items()})
        groups.append(group)
        provided[target].update(exports(group))
    data["exports"] = groups
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 使用 SDK 相同的块映射和行内数组，兼容 Zig 的 TBD 解析器。
    lines = ["--- !tapi-tbd\n", "tbd-version: 4\n",
             f"targets: {json.dumps(data['targets'])}\n",
             f"install-name: {json.dumps(data['install-name'])}\n"]
    for key in ("current-version", "compatibility-version"):
        if key in data:
            lines.append(f"{key}: {data[key]}\n")
    lines.append("exports:\n")
    for group in groups:
        for key, values in group.items():
            prefix = "  - " if key == "targets" else "    "
            lines.append(textwrap.fill(
                json.dumps(values), width=84, initial_indent=f"{prefix}{key}: ",
                subsequent_indent="                     ",
                break_long_words=False, break_on_hyphens=False,
            ) + "\n")
    dest.write_text("".join(lines) + "...\n")
    return provided
