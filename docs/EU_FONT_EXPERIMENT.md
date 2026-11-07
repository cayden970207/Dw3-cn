# 欧版原版与新中文字库实验

## 证据范围

本实验在 `experiment/eu-original-new-font`，基于原有 `c1f876d`，不替代原汉化分支。用户提供的欧版 7z SHA-256 为 `7e68a2caab41d3946a7ca4cf6697ae2c8e3ff990563da07c3e9e64be33d24837`；新解出的只读镜像为 `fb70dc9a995aed628cf515cabc87c7b14e5142559076ec394dfe793ec3e26a04`。其 EXE 为 `SLES-03936`，文件表 2,382/2,382 与目录记录匹配，50 个已索引英文资源有 13,084 个条目。详见 `reports/SHARE_THIS_eu_original_baseline_report.json`。

这里的「原版」指用户提供并作为本轮基线的欧版文件；EXE 与固定参考哈希一致，但整盘尚未与权威发行版全盘哈希对照，不能据此断言此前从未修改过。

新欧版镜像、原汉化 ZIP、所有既有私有文件和测试成果均不覆盖。原汉化镜像与欧版不是同一基线；原汉化 ID 647 的扩展尾部不能用于假定欧版空闲空间。本实验没有把任何镜像、原游戏文字、字库图像、字体文件或记忆卡放进仓库。

## 字体来源和覆盖

离线字库优先使用 [Ark Pixel](https://github.com/TakWolf/ark-pixel-font) 的 12px 等宽简体版本 `2026.10.25`。它本身并不涵盖本次全部目标字符，故以 [Fusion Pixel](https://github.com/TakWolf/fusion-pixel-font)、[Noto Sans CJK SC](https://github.com/notofonts/noto-cjk) 和 [GNU Unifont](https://unifoundry.com/unifont/index.html) 依序补缺。各源固定 SHA-256 和许可证记录在 `reports/SHARE_THIS_eu_font_generation_report.json`；这四个来源均可采用 SIL OFL 1.1，Unifont 还提供另一许可。原版版权声明和授权文本保留在本机字体来源目录。若将来分发衍生字体，应随分发物满足各来源的授权、署名和保留名称要求；目前不分发字体数据。

| 项目 | 本次结果 |
| --- | ---: |
| 离线原生 12×12 字符数 | 32,950 |
| 基本汉字 U+4E00～U+9FFF | 20,992 / 20,992 |
| 汉字扩展 A U+3400～U+4DBF | 6,592 / 6,592 |
| GB2312 字符集合 | 7,445 / 7,445 |
| 来源可提供的部分增补汉字 | 4,545 |
| 12px 字源：Ark / Fusion / Noto / Unifont | 22,856 / 751 / 8,190 / 1,153 |

GB2312 在这里只是**字符覆盖目标**，不是对游戏原始编码的猜测。补充平面只选取本次字源能够提供的部分字符，不能说覆盖所有 Unicode 汉字。942 个原生字形有缩小处理，密集笔画可能难辨；逐格校验检测数据和来源像素是否一致，不等于所有字形已人工判读或在游戏里可读。

本机同时生成 129 个 256 字位的 4bpp 图形块、1bpp 紧凑块和 48px 离线图集。独立校验器重新绘制每个字，并分别解包比较全部 32,950 格的 1bpp 和 4bpp 像素；48px 图集逐格与来源重新绘制的结果对比。这个验证与运行时加载是两回事。48px 图集优先 Noto，并非已完成的 Ark 高清替换包，也未在 DuckStation 中测试。

字体对照预览只在 `local/eu-original-font/font-source/PRIVATE_font_comparison.png`。其中前两组方舟和 Fusion 的样本文字点阵恰好相同；最后的 Noto 48px 为离线示意，并非游戏画面。生成/校验报告分别是 `reports/SHARE_THIS_eu_font_generation_report.json` 和 `reports/SHARE_THIS_eu_font_verification_report.json`。

## 实际游戏中的四字样机

工具从固定欧版镜像重建新的私有测试副本，读取上述新字库中「卡片图鉴」的四个 Ark Pixel 格子，并将其嵌入四个实验字位，修改图鉴标题。测试副本 SHA-256 `fe6af06114d3e2a055012700d7d80eea1a42d86fa0bcac498c3093dfc50ec7e1`；原始镜像和原 7z 哈希前后相同。全盘比较确认改动仅在批准的字体、标题和受影响扇区校验内，所有修改扇区的 EDC/ECC 修改前后均有效。第一版构建曾因差异区间横跨相邻的批准范围而被拒绝，后改为只合并相邻或重叠范围后重建；未放开间隙检查。

四个字位原用于日文字形，实验仅检查它们不出现于 50 个已索引英文资源，并核对纹理矩形与 EXE 的字形表不冲突。**其他语言、动态文本和全部场景未覆盖**。这不是通用中文字码表，也不是对原字库的完整替换；不要作为正式版使用。

使用 PCSX-Redux `a74a0587` build 433、OpenBIOS、从隔离条件存档复制出的记忆卡，实际进入图鉴。新标题 181 个白色像素与字库中四格的预测完全一致，实际绘制推进 44px；基线与测试画面仅标题区域有差异。实际退出、重进后，再从第 1 页翻到第 27 页并返回第 1 页、进入卡组和返回场景。访问后观察游戏内新的写卡开始和完成，模拟器进程退出，再从测试 CUE 冷启动，游戏内读档并重开图鉴；复读文字像素与已核对画面一致，存档游戏时间也证实读入的是后一次存档。私有截图和记忆卡保留本机。

两次回放均有 12 条手柄 `43` 提示；动作成功依据是实际页码、状态、写卡与冷读结果，不仅是按键命令。第一阶段有两次固定帧的回放监听器栈重置，日志说明是该版本回放机制已知处理；冷启动阶段零次。样机创建、画面证据及存档回归分别见 `reports/SHARE_THIS_eu_font_prototype_creation_report.json`、`reports/SHARE_THIS_eu_font_runtime_report.json` 和 `reports/SHARE_THIS_eu_font_save_regression_report.json`。人工步骤见 `docs/MANUAL_TEST_EU_FONT_PROTOTYPE.md`。

针对用户指出难辨的「槽」，另从同一欧版基线分别建立两个**相互独立**的私有样机，用图鉴标题临时展示「记忆插槽」，实际记忆卡界面未改。一个沿用 font 1 原有每字 11px 推进；另一个只在该样机中把四个实验字位的 font 1 指标改成每字 12px。两版都真实进入游戏，四个字的 200 个白色像素相同；12px 版本每字格之间有 1px 空列，总推进从 44px 增为 48px，修改扇区的 EDC/ECC 均有效。放宽字距只能减少相邻字贴合，不能给 12×12「槽」增加笔画细节；该窗口之外及其他字体均未验证，两版也没有分别做存档冷读回归。真实画面局部的最近邻 8 倍对照保留本机 `local/eu-original-font/runtime-slot-spacing12-v1/PRIVATE_spacing_comparison_v2.png`，这不是高清纹理替换。脱敏验证见 `reports/SHARE_THIS_eu_glyph_spacing_report.json`。

## 还未接上的部分

已实现只用于离线实验的新三字节字位编码提案：标记加两个非零字节可索引 65,025 个字位。提案逐条核验了 50 个真实欧版英文资源的 13,084 条记录、35,567 个控制 token 的原字节与边界，及全部 32,950 个新字库索引的编码回环；标记在这 50 个资源的 token 边界没有冲突。**动态文本、其他语言、SJIS、游戏运行时代码并未因此获证。** 原欧版 EXE 对该标记仍使用原有 fallback，既不消费三字节，也不会绘制新字库。详见 `reports/SHARE_THIS_eu_codec_proposal_report.json`。

四字标题原位置为 12 字节，新提案含终止符需要 13 字节。已在内存里按原资源指针重建该真实九条目表，并保留其他条目和各条目的非文本尾部：文件从 98 字节增至 102 字节，新的相对指针保持四字节对齐。结果只在本机，不写镜像；是否适用于实际 CD 文件范围、ISO 元数据和游戏加载路径尚未验证。见 `reports/SHARE_THIS_eu_resource_rebuild_report.json`。

加载完整字库还需要独立实现并验证游戏侧的解码、字符前进、动态插入、宽度/换行和绘字缓存。129 个 4bpp 块共 2,377,728 字节，超过 PS1 的 2 MiB 主内存；按扇区对齐的 1bpp 块共 792,576 字节，亦会占用游戏名义上约 1,390,592 字节堆区的大部分，还必须为场景、资源、对象和显存留空间。可考虑按需加载与缓存，但尚无足以证明全场景安全的运行证据。现有原汉化通用写回封锁保持不变。

在同一测试副本的第二次翻页、存档和冷读回放中，额外通过只读监听器每 30 帧检查欧版 EXE 的堆链表，共取得 819 个有效样本，涵盖两个独立进程。图鉴阶段的 329 个首进程样本中，当前最大连续空闲块均小于无填充整库 1bpp 的 593,100 字节；其中最小值为 72,480 字节。实测表明仅看总堆大小不足以断言整库可常驻，但游戏分配器可能释放 CD 缓存，当前空闲块不足也不能单独证明清缓存后无法分配；本轮没有实际为整库分配内存。其他场景、采样间隙和显存占用未覆盖，见 `reports/SHARE_THIS_eu_heap_observation_report.json`。

**状态：字形数据已生成；整套字库尚未在游戏内工作，全游戏汉化未开始。** 四字标题是字库样机，不是全游戏完成度。

## 在本机复现

Python 3.11 或更高版本可运行项目脚本；本次使用 Python 3.14、Pillow 13 和 fonttools 4.68。依赖和字体来源可以单独放在忽略的 `local/` 下，执行时设置 `PYTHONPATH=local/python-deps:.`。以下占位路径应指向固定哈希的本地字体，输出目录必须是尚不存在的 `local/` 子目录：

```sh
PYTHONPATH=local/python-deps:. python3 tools/build_new_font_banks.py \
  /本地/ark.otf /本地/fusion.otf /本地/noto.otf /本地/unifont.otf \
  local/eu-original-font/NEW-font-build

PYTHONPATH=local/python-deps:. python3 tools/verify_new_font_banks.py \
  local/eu-original-font/NEW-font-build /本地/ark.otf /本地/fusion.otf \
  /本地/noto.otf /本地/unifont.otf \
  local/eu-original-font/NEW-font-build/SHARE_THIS_verification.json
```

`tools/eu_banked_codec.py` 和 `tools/eu_text_rebuild_experiment.py` 只分析本地基线和私有索引；`tools/eu_font_prototype.py` 创建受哈希约束的新副本；`tools/replay_eu_font_prototype.py`、`tools/collect_eu_font_prototype.py` 和 `tools/replay_eu_font_save.py` 生成或核对实际画面与存档证据。后者的 `--heap-probe` 只读采样堆链表，可由 `tools/eu_heap_report.py` 汇总。各脚本支持 `--help`。任何文本资源、画面、字库和记忆卡输出须继续留在 `local/`。
