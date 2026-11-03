# ZhuYu-sdk

为 [ZhuYu](https://github.com/jiangbo/ZhuYu) 整理跨平台构建所需的 SDK 材料，
包括头文件、链接材料和 APK 打包工具。

本仓库独立维护这些材料的版本、来源、许可证和提取流程，通过 Release
按平台分发压缩包。引擎代码更新时，可以继续使用已有 SDK 版本。

## 材料目录

| 目录 | 内容 | 当前来源 |
| --- | --- | --- |
| `linux/` | GNU、musl 的系统库头文件及来源说明 | Debian 11、Alpine Linux 3.22 |
| `android/` | 四种架构的头文件、系统占位库和 CRT 文件 | Android NDK r28c，API 26–35 |
| `macos/` | 头文件、完整 libSystem 链接描述和 SDK 元数据 | macOS 14.0 SDK |
| `ios/` | 真机、模拟器的头文件、完整 libSystem 链接描述和 SDK 元数据 | iOS 17.0 SDK |
| `apk/` | ARSCLib、apksigner 及许可说明 | ARSCLib 1.4.0、Build Tools 36.0.0 |

这些材料用于编译和链接，不包含完整的开发环境。Apple 的 `.tbd` 和 Android
的系统占位库描述目标系统提供的接口，应用运行时使用目标系统的真实系统库。

各目录中的 `SOURCE.txt` 记录材料来源和维护方式，`FILES.txt` 记录提取文件。
上游许可证和版权声明随材料保留。

## 与 ZhuYu 的分工

本仓库负责 SDK 材料本身。以下内容继续由 ZhuYu 维护：

- 平台构建接入代码。
- Linux 的 `.def` 链接符号清单及其生成工具。
- Apple 的精简 Framework TBD、`libobjc.tbd` 及其生成工具。
- 应用构建、资源处理和打包逻辑。

SDK 头文件提取与引擎链接定义生成分别执行。引擎增加系统接口调用时，
如果现有 SDK 已有对应声明，可以只更新引擎中的链接定义。

## 提取材料

在本仓库根目录执行，使用 Python 3.10 或以上。
默认更新对应平台目录，也可以通过 `--output` 先写入其他目录，再核对结果。

Linux 自动下载固定版本的开发包，按清单提取头文件：

```sh
python linux/extract.py all
```

也可以将 `all` 改为 `gnu` 或 `musl`。
下载地址和包校验值定义在各自的 `packages.json`，头文件选择定义在
`headers.json`。下载缓存位于 `linux/.cache/`，不提交 Git。

Android 从本地 NDK 提取：

```sh
python android/extract.py /path/to/android-ndk
```

macOS 从原始 SDK 提取：

```sh
python macos/extract.py /path/to/MacOSX14.0.sdk
```

iOS 分别读取真机和模拟器 SDK。模拟器 libSystem 的接口展开需要 PyYAML：

```sh
python -m pip install PyYAML
python ios/extract.py /path/to/iPhoneOS17.0.sdk /path/to/iPhoneSimulator17.0.sdk
```

普通提取不需要 Zig、Sokol 或 ZhuYu 源码。Android 和 Apple 的提取脚本会检查
原始 SDK 版本及头文件内容是否匹配清单。

## 更新头文件清单

Android、macOS 和 iOS 的 `scan.py` 用于分析 Sokol 源码的头文件依赖，
需要原始 SDK、Sokol 的 `src/sokol/c` 目录以及 Zig。
它们只更新头文件清单，不提取材料，也不生成引擎的链接定义。

Linux 的头文件选择直接在 `headers.json` 中维护。
更新 SDK 版本或清单后，重新提取并核对文件，同时更新来源和许可说明。
各平台的参数与命令示例见对应的 `SOURCE.txt`，也可运行脚本的 `--help`。

根目录的 `apple.py` 是 iOS、macOS 共用的扫描和提取实现；
`apple_tbd.py` 负责解析 TBD、展开模拟器的完整 libSystem 接口。
平台目录内的 `extract.py`、`scan.py` 是各自入口。

## 更新 APK 工具

APK 工具手动下载、复制，不提供提取脚本：

- 从 ARSCLib 对应版本的 Release 下载 JAR，保存为 `apk/ARSCLib.jar`，
  同时更新 `ARSCLib-LICENSE.txt`。
- 从 Android SDK Build Tools 中复制 `lib/apksigner.jar`，
  并将对应的 `NOTICE.txt` 保存为 `apk/Android-NOTICE.txt`。

更新时同步修改 `apk/SOURCE.txt` 中的版本和来源。
运行 APK 打包工具需要 JDK 11 或以上。

## 发布 Release

材料更新、核对并提交后，推送以 `v` 开头的版本标签，例如：

```sh
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions 会打包标签对应的仓库内容，创建 Release 并上传五个附件：

- `linux.tar.gz`
- `android.tar.gz`
- `macos.tar.gz`
- `ios.tar.gz`
- `apk.tar.gz`

每包保留对应平台的顶层目录，包含构建材料、来源和许可说明。
维护脚本、提取清单和下载缓存不进入压缩包，也不生成额外的哈希文件。
发布流程只打包已提交的材料，不重新下载或提取上游 SDK。

## 许可证

本仓库自有代码使用 [LICENSE](LICENSE) 中的许可证。
收录的头文件、JAR 和其他上游材料遵循各自的许可证，详见对应目录中的
版权声明、`NOTICE`、`licenses/` 和其他许可文件。
