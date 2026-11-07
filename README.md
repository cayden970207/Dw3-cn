# Dw3-cn —《数码宝贝世界 3》汉化补完

基于用户提供的欧版既有汉化继续补翻、校对和测试。本仓库保存原创工具、阶段记录和可复现验证，不分发游戏本体。

> **当前为 P0/P1 工具与扫描流程，尚未交付新一版可玩汉化补丁。自动测试通过 ≠ 游戏文字和流程通过。**

## 已实现的工具

| 工具 | 用途 |
|---|---|
| `tools/inspect_zip.py` | 不提取镜像，校验 ZIP、内部镜像指纹和扇区布局候选 |
| `tools/verify_base.py` | 对解包后的镜像计算 SHA-256，初筛扇区格式 |
| `tools/scan_text_tables.py` | **只读**扫描欧版 ISO 的目录、候选文件表、50 类英语槽位文本表；支持 ZIP |
| `tools/triage_english.py` | 使用本地私有扫描结果建立**疑似英文残留**人工审核队列，不自动判定漏译 |
| `tools/qa_text.py` | 检查经过人工录入的译文、控制码、字形、宽度、行数与字节容量 |
| `tools/byte_patch.py` | 生成/应用哈希绑定的**等长二进制差异补丁**，不修改输入镜像；非 PS1 回写引擎 |
| `tools/psx_sector.py` | 验证并重算 2352 字节 Mode 2 Form 1 的 EDC/ECC（本地镜像必需） |
| `tools/apply_text_bytes.py` | 在精确 SHA-256 与私有字形字节映射验证后，安全写回等长/缩短条目，再校验全部文本 |
| `tools/prepare_translation_patch.py` | 将**人工核对过的 Unicode 中文**用真实字形对照映射成私有补丁清单，逐字检查缺字、容量和原控制码 |

## 工作流程

Python 3.11+，仅使用标准库。所有实际游戏文件保留在**本地**。

```sh
# 第一步：对自己拥有的汉化 ZIP 建立结构报告（不输出原版文本）
python tools/scan_text_tables.py "/path/to/数码宝贝世界3 欧版汉化.zip"

# 第二步：在 Git 忽略的本地目录中创建供人工审查的原始文本索引
python tools/scan_text_tables.py "/path/to/数码宝贝世界3 欧版汉化.zip" \
  --private-dump local/scan-private.json

# 第三步：把疑似英文的条目单独整理成待审清单
python tools/triage_english.py local/scan-private.json \
  --queue local/english-candidates.json

# 静态检测工具示例（只使用人为合成的测试数据）
python tools/qa_text.py data/catalog.example.json --profile data/profile.example.json
python -m unittest discover -s tests -v
```

扫描器在 ZIP 中只选择 ISO/BIN/IMG 镜像，暂存到临时目录进行结构分析，完成后清理，所需临时磁盘空间可能超过 ZIP 大小。详情见 [SCAN](docs/SCAN.md)、[TRIAGE](docs/TRIAGE.md)、[TRANSLATE](docs/TRANSLATE.md) 和 [WRITE](docs/WRITE.md)。

**不要将示例配置用于真实游戏写回。** 自定义中文编码、中文字库和动态窗口尺寸必须从原汉化版本验证。扫描器不能改变游戏镜像，英文候选报告也不能直接证明某条文本漏译。

## 测试证据

2026-11-07 GitHub Actions 对提交 `0e7be21` 的合成测试运行成功：

- Python 3.11：123 tests / OK
- Python 3.12：78 tests / OK
- [查看工作流](https://github.com/cayden970207/Dw3-cn/actions/runs/42357717766)

全部属于**合成数据单元/端到端工具测试**（含 50 文本资源的人工生成光盘、两种扇区模式及 ZIP→新镜像流程），未包含用户原镜像、真实译文或模拟器运行。详见 [STATUS](docs/STATUS.md)。

## 项目目标与边界

最终目标：全游戏简体中文；没有已知乱码、缺字、文字越框、错误分页或由汉化引发的流程问题。涵盖主线、支线、卡牌、战斗、菜单、存读档与通关后内容。不能在没有全流程验收时承诺绝对零 Bug。

**不上传**游戏镜像、BIOS、完整解包素材、原版字库、个人存档、密钥及私有 `raw_hex` 文本。正式发布补丁必须绑定实际用户镜像的精确 SHA-256；原汉化来源及作者核实后保留署名，不把已有成果冒称原创。

阶段与验收流程：[ROADMAP](docs/ROADMAP.md)。当前阻塞与证据边界：[STATUS](docs/STATUS.md)。
