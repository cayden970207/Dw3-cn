# Dw3-cn —《数码宝贝世界 3》汉化补完

基于用户提供的欧版既有汉化继续补翻、校对和测试。本仓库保存原创工具、阶段记录和可复现验证，不分发游戏本体。

> **已收到用户本机的真实镜像结构扫描报告；下一步是实际中文字库与编码恢复。尚无新增可玩汉化补丁的验收结论。**

## 当前里程碑

2026-11-07，用户提交两份本地扫描报告。报告记录的精确镜像 SHA-256 为：

```
c7130c532f42d7c01f393bf067b073a1e4b924607a71e2287b0c3792468777fc
```

50 个预期 ENG 槽位资源通过现有结构解析，索引 13084 条。七组语言共 350 个对应资源，347 个可解析，合计 91059 条记录；这不是独立待翻译句子的数量，也不是全游戏文字覆盖率。

文件表 ID647 在 ISO 声明末尾之后、物理镜像范围内，占 91 个扇区。上游原版资料将 647（0x287）映射到字形图像资源：**这是重定位/扩展字库的重要调查线索，不是已确认的汉化字体结论。该区域禁止截断、清理或当作空闲空间使用。**

本轮确认的是用户提供的报告，未在当前 ChatGPT 容器重新执行原镜像。真实基线记录：[user-cn-c7130c53.json](data/baselines/user-cn-c7130c53.json)。

## 下一步：在已有原镜像的本地工作区恢复中文路径

直接执行 [NEXT_FONT_RECOVERY](docs/NEXT_FONT_RECOVERY.md)，复用已经生成的私有索引，不要重复要求用户做首轮扫描：

1. 确认选择中文后的实际语言状态及资源加载路径。
2. 按 EXE 文件表只读追查 ID647，结合实际绘字代码恢复有证据的部分字形映射及度量。
3. 对真实文本做无变化编解码回环，分别记录未知 token、Unicode 覆盖及显示核验范围。
4. 条件满足后，选一处可稳定触发的漏译，生成全新测试副本并实际验证画面和操作。

当前报告没有原始字库/文本字节，不能仅凭报告生成汉化补丁。实际编码规则仍须验证，不能直接使用示例配置或把原版字符集当作汉化码表。

## 已实现的工具

| 工具 | 用途 |
|---|---|
| `tools/inspect_zip.py` | 不提取镜像，校验 ZIP、内部镜像指纹和扇区布局候选 |
| `tools/verify_base.py` | 对解包后的镜像计算 SHA-256，初筛扇区格式 |
| `tools/scan_text_tables.py` | 只读扫描欧版 ISO 的目录、文件表及 50 类英语槽位文本表；支持 ZIP |
| `tools/triage_english.py` | 建立疑似英文残留的人审队列；不自动判定漏译 |
| `tools/qa_text.py` | 检查译文、控制码、字形、宽度、行数与字节容量 |
| `tools/byte_patch.py` | 生成/应用哈希绑定的等长二进制差异补丁；非 PS1 回写引擎 |
| `tools/psx_sector.py` | 验证/重算 2352 字节 Mode 2 Form 1 的 EDC/ECC |
| `tools/apply_text_bytes.py` | 哈希与字节前置条件绑定的等长/缩短回写，再扫描全部文本 |
| `tools/prepare_translation_patch.py` | 根据已经核实的字形对照，把人工校对的 Unicode 译文编码成私有清单 |
| `tools/local_probe.py` | 原镜像只读诊断；用户已经完成首轮，保留作重现入口 |

写回工具的实际汉化编码兼容性、字形宽度及运行流程仍需核实。结构扫描成功不等于这些门槛已通过。

## 参考工作流程

Python 3.11+，已有脚本仅使用标准库。所有实际游戏资源保留在本地。

```sh
# 已完成首轮扫描；以下命令只用于需要时复现，不是当前的新任务。
python3 tools/local_probe.py "/your/path/数码宝贝世界3 欧版汉化.zip"

# 合成数据静态测试，不包含游戏镜像。
python tools/qa_text.py data/catalog.example.json --profile data/profile.example.json
python -m unittest discover -s tests -v
```

分享 `SHARE_THIS_report.json` 等脱敏结构报告；`PRIVATE_DO_NOT_UPLOAD.json` 含游戏原始字节，留在本地且不提交公开仓库。ZIP 扫描会暂存镜像并在完成后清理，需要足够临时空间。

说明：[LOCAL_TEST](docs/LOCAL_TEST.md)、[SCAN](docs/SCAN.md)、[TRIAGE](docs/TRIAGE.md)、[TRANSLATE](docs/TRANSLATE.md)、[WRITE](docs/WRITE.md)。

## 测试与证据边界

历史工具测试记录：提交 `746726a` 的 [工作流 42358678163](https://github.com/cayden970207/Dw3-cn/actions/runs/42358678163) 在 Python 3.11 和 3.12 各通过129项合成测试。它不包括用户原镜像、真实译文或模拟器运行，也不是本次文档更新后的新测试结果。

用户本机的真实结构扫描是另一项证据；它不证明全部文字已汉化。当前实质进度见 [STATUS](docs/STATUS.md)，禁止把新增工具测试数等同于汉化完成度。

## 项目目标与边界

最终目标：全游戏简体中文；发布时无已知乱码、缺字、越框、错误分页及汉化引发的流程异常。覆盖主线、支线、卡牌、战斗、菜单、存读档、隐藏事件和通关后内容。不能在没有全流程验收时承诺绝对零 Bug。

**不上传**游戏镜像、BIOS、完整解包素材、字体文件、完整字库图集、个人存档、密钥及私有 `raw_hex` 文本。正式补丁须绑定精确输入 SHA-256。原汉化作者和版本仍待核实，确认后保留署名及来源，不把既有成果冒称原创。

阶段规划：[ROADMAP](docs/ROADMAP.md)。下一轮具体任务：[NEXT_FONT_RECOVERY](docs/NEXT_FONT_RECOVERY.md)。
