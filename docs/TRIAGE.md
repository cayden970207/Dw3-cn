# P1：英文漏译候选队列（不能直接作为漏译证据）

先按 [SCAN.md](SCAN.md) 在本地生成包含 `raw_hex` 的私有数据：

```sh
python tools/scan_text_tables.py "/path/to/用户原汉化.zip" --private-dump local/scan-private.json
python tools/triage_english.py local/scan-private.json --queue local/english-candidates.json
```

普通命令行只打印各文件的候选数量；人工审核清单保存在本地 `local/english-candidates.json`，包含条目 ID 和可能的英文字样，不得提交到公开仓库。

本启发式扫描仅尝试识别未改动的欧版 1 字节拉丁字形：`04..0D` 数字、`0E..27` 大写字母、`28..41` 小写字母、`01 01` 空格，并跳过已知控制码。它不是中文字库解码器。用户的汉化版本如改过编码，会出现误报或漏报。**“候选”不代表确认漏译**，也不能直接用于写回。

## 人工审查步骤

1. 根据镜像 SHA-256 记录精确底版。
2. 审核优先顺序：`CARDGM`、`CARDST`、`CRDABM`、`CRDSHP`、`DIGINF`、`DGLABO`、`MEMCRD`，之后扫描其他模块。
3. 确认文字出现的场景；区分人名、数码兽英文缩写、正常显示的拉丁名称和真正漏译。
4. 单独核查对应中文字库、宽度、动态插入和选择框。
5. 未取得稳定可复现的原文坐标、控制码和实际截图，不做自动回写。

发现源数据解码错误时，命令返回非零状态，不能把这些条目标成已通过。
