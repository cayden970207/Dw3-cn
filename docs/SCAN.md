# P1：只读扫描欧版文本资源

命令：

```sh
python tools/scan_text_tables.py "/path/to/数码宝贝世界3 欧版汉化.zip"
python tools/scan_text_tables.py "/path/to/your-original-image.bin" --expect <64-character-sha256>
python tools/scan_text_tables.py "/path/to/数码宝贝世界3 欧版汉化.zip" --private-dump local/actual-raw-text.json
```

**只读、非游戏测试。** 普通输出列出镜像 SHA-256、扇区模式、游戏 EXE 的文件表匹配度，以及可识别的 `COUNTRY/ENG/ES*.BIN` 文本表和各表计数；不输出文本内容或游戏文件的二进制字节。

`--private-dump` 在本地生成含条目 `raw_hex` 的 JSON（仅为研究现有中文字库、编码及文本映射所需），必须留在 Git 忽略的 `local/`、`input/` 等目录中。原素材、整份镜像及存档不得提交。本工具按需把压缩包内唯一镜像**暂存到系统临时目录**，最多 2 GB，扫描结束立即清除；需要可用磁盘空间。

## 检查逻辑

1. ISO9660 PVD 的 LBA16 和 2048 / 2352 扇区用户数据偏移。
2. ISO9660 path table 及各个目录条目——欧版可能有不能直接从普通根目录枚举的 `AAA/`。
3. `SLES_039.36` 的 PS-X EXE 签名及原版参考程序内的 `filetable_lba`、`filetable_sectors` 候选地址。
4. 50 组英语槽位文本文件（通常路径为 `.../COUNTRY/ENG/ES*.BIN`），其中普通 counted tables 和 11 类剧情事件容器按不同结构检查。
5. 每条原始文本的字节长度及 SHA-256，便于后续无损比对。**不解码汉字、也不作英文漏译判断**。

## 已知限制

- 可识别的路径与文件表结构来源于 [gascarcella/dw2003recomp 的格式说明](https://github.com/gascarcella/dw2003recomp/blob/main/docs/FORMATS.md)；**在用户修改过的汉化镜像上仍需实测**。尤其中文字库与控制码可能与原版不同。
- 文件表候选匹配率小于 97%、或存在超出镜像边界的文件表范围，会使文字资源清点标记为不完整。即使达到阈值，未匹配 ISO 目录的文件表记录也会列于 `boot.filetable_unmatched` 并产生警告；须单独调查其用途和影响。
- `inventory_complete` 仅表示 50 个参考文字资源通过结构检查，**不代表整个文件表百分之百匹配、所有游戏文字都已找到、汉化完成或实际游戏验证通过**。这些文字资源发生缺表、偏移越界、路径或结构异常时，不会标为完整。
- 原有 P0 补丁验证、字形映射以及游戏内测试仍在进行。不能把这份工具当作文字回写器；它绝不修改游戏镜像。
- 表条目仍含原版内容；即使是 `raw_hex` 也不得上传公开仓库。

## 下一阶段入口

获得用户镜像的精确 SHA-256 和文件表索引后，在隔离环境内建立实际编码表，完成「原字节→可编辑文本→原字节」逐条不变回环，再为卡牌/指南第一批译文生成哈希绑定补丁并用模拟器验证。
