# Dw3-cn —《数码宝贝世界 3》汉化补完

基于用户提供的欧版既有汉化继续补翻、校对和测试。本仓库保存原创工具、阶段记录和可复现验证，不分发游戏本体。

> **当前为工程基线，不是新版汉化补丁。工具的单元测试通过 ≠ 游戏已经完整汉化。**

## 已归档

- `tools/verify_base.py`：只读计算镜像 SHA-256，初筛 ISO9660 扇区布局与启动编号候选。
- `tools/qa_text.py`：检查译文缺失、控制码顺序、缺字、字宽、每页行数、编码字节预算及重复编号。
- `tests/`：纯合成数据的单元测试，不需要游戏文件。
- `docs/STATUS.md`：已提交成果、历史记录与缺失证据。
- `docs/ROADMAP.md`：阶段与验收标准。
- `docs/FORMAT.md`：翻译目录和字形配置格式。

## 使用

Python 3.11 或更高版本，仅使用标准库。

```sh
python tools/verify_base.py "/path/to/local/game-image.bin"
python tools/verify_base.py "/path/to/local/game-image.bin" --expect <精确的64位SHA256>
python tools/qa_text.py data/catalog.example.json --profile data/profile.example.json
python -m unittest discover -s tests -v
```

示例数据全部是人工构造的测试夹具，**不是从游戏提取的文本、编码或字库**。检查器不会修改镜像，也不构建补丁。实际镜像配置尚未恢复，不能将示例配置用于游戏。

`qa_text.py --strict` 会将尚未复核的译文、示例字形等 pending 项也视为失败。默认成功仅表示静态错误列表为空；报告始终标记 `gameplay_verified: false`。

## 项目目标

完整简体中文；发布时无已知乱码、缺字、越框、错误分页及汉化引发的流程异常。必须覆盖主线、支线、卡牌、战斗、菜单、存读档和通关后内容。不能承诺绝对零 Bug，也不能用开场测试代替全流程验证。

## 不上传

游戏镜像、BIOS、完整解包素材、原版字库、个人存档、密钥。后续补丁必须绑定精确输入 SHA-256。原汉化作者和版本尚待核实，确认后保留署名及来源，不将既有成果冒称原创。

完整状态与证据边界见 [STATUS](docs/STATUS.md)。
