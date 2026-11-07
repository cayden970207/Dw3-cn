# c7130c53 汉化镜像：字库与文本恢复

本页记录对用户实际镜像的分析。原 ZIP、原游戏镜像、字体图像、原始文本索引、运行截图、记忆卡、局部二进制差异和测试镜像均留在 Git 忽略的 `local/`。公开文件只含原创工具、经过人工确认的少量单字映射和脱敏元数据。原版参考为 [dw2003recomp](https://github.com/gascarcella/dw2003recomp/blob/841521ab2071096038316b93b8f778255b85730a/docs/FORMATS.md)，其结论均须与用户镜像核对。

## 实际路径与字体

源 ZIP SHA-256 为 `aaf88ce1aa7492e22ec83722f8cf2b8985c08fac9cc193881dabbbafc4bec94f`，解压镜像为 `c7130c532f42d7c01f393bf067b073a1e4b924607a71e2287b0c3792468777fc`，EXE 为 `c902e59f9dc8f35a6fe2792604c485adf09a71d59c4f0769fb9654803bc9713e`。EXE 文件表 ID 647 从 LBA 294280 起共 91 个 Mode 2 Form 1 原始扇区，逐个检查同步头、复制的子头和 EDC/ECC。原始扇区块 SHA-256 为 `4a3462be607f559ac7cac81de03f2036f0df51c0ab999ddfaaaef387348db15a`；其 2048 字节用户数据串 SHA-256 为 `3583f16b76882114e369fb07fb686271fa62c12cd107aca05281d257f08fa5f0`。

实际 EXE 的字体加载钩子 `0x8001D234 → 0x80010860` 读取 ID 647；绘字钩子 `0x8001A610 → 0x80010A20` 使用尾部新增字形。资源前部两张 RLEN/TIM 解压后与目录 CMFONTTM 的对应图像哈希一致，但新增字形从用户数据偏移 41604 开始，每字 72 字节，4bpp、低半字节先行、12×12、共 1,997 个；数组 SHA-256 为 `d3fc6b6bb9cb6d0750e2375984b90df949f7102ad7cb632491c80ded0e23efe5`。读目录旧副本不能替代实际尾部加载路径。

本机从语言菜单纹理和运行截图目视确认第 0 项显示「中文」；实际 `CNTY_SEL.PRO` 选择表写入状态 2，独立运行日志确认选择后为 2。实际卡片图鉴 overlay 的加载调用以状态为偏移，获得 ID 24、31、38；它与固定的上游 `STCRDABM.PRO` SHA-1 `0e58e7aef70165be0bb6b95d59236941ab648b28` 相同，因此可用对应源码交叉理解窗口 0：字体 1、原点 (16,25)、取 ID 38 条目 1，且该窗口未调用通用测宽。另一条独立的静态对白链为状态 2 → ID 234/LBA 2538 → 条目 461，其中包含已审阅的「亚、古、兽」字形；实际对话触发还未验证。不得将这些路径推广为全部语言资源。

## 分词和映射

实际 EXE 在 `0x8001D370 → 0x800109F4` 识别 `F0..FF` 前导双字节，绘制索引为 `(lead - 0xF0) * 252 + trail - 4`，每字绘制步进 12 像素。常规编码选择 trail `04..FF`；其他可形成同一索引的字节对在回环时仍原样保留。对已调查的存储文本路径，复制使用 `strlen`，因此内部 NUL 不可安全当作控制参数或字形字节；未知动态文本路径不由此推断。实际控制表含标记总长度为 `1,2,3,2,5,3,3,2,3,2`，文件文本路径的 `03` fallback 消耗一字节。工具 `text_codec.py` 只对精确哈希启用该汉化变体，`prepare_translation_patch.py`、`apply_text_bytes.py` 和 `triage_english.py` 共用分词规则。

以下是用户目视确认的局部字形，不是对其他字符的猜测：

| 字 | glyph 索引 | 标准双字节 |
| --- | ---: | --- |
| 亚 | 81 | `F055` |
| 古 | 121 | `F07D` |
| 兽 | 1 | `F005` |
| 数 | 26 | `F01E` |
| 码 | 28 | `F020` |
| 卡 | 48 | `F034` |
| 片 | 71 | `F04B` |
| 图 | 1082 | `F44E` |
| 鉴 | 1913 | `F799` |
| 打 | 52 | `F038` |
| 开 | 135 | `F08B` |
| 回 | 1121 | `F475` |
| 有 | 13 | `F011` |
| 包 | 345 | `F161` |
| 买 | 883 | `F383` |
| 一 | 17 | `F015` |
| 张 | 318 | `F146` |
| 编 | 1343 | `F557` |
| 辑 | 1192 | `F4BC` |
| 组 | 262 | `F10E` |
| 没 | 34 | `F026` |
| 称 | 931 | `F3B3` |
| 金 | 291 | `F12B` |
| 不 | 7 | `F00B` |
| 足 | 577 | `F24D` |
| 钱 | 712 | `F2D4` |
| 改 | 621 | `F279` |
| 名 | 378 | `F182` |
| 选 | 523 | `F217` |
| 择 | 756 | `F304` |

映射来源、图块边界和用户确认记录见 `data/baselines/c7130c53-reviewed-glyphs.json` 和字库报告。实际绘制和人眼识别是不同证据；第二批的「金、钱、不、足、选、择、改、名」已在对应画面进一步验证。用户在真实相邻上下文辅助下确认了最初模糊的 523、756；其余 1,967 个字形的 Unicode 身份仍未确认。字形 164、1489 和 1151 仍因判读不确定，没有加入映射。

## 真实回环与写入限制

`roundtrip_cn.py` 从既有索引指定的 50 个资源中，逐一检查真实资源哈希、子表、指针、字符串边界、终止符和 token；未知 token 原样保留。全部 13,084 条条目回环相同，50 个完整文件以原文件为模板替换相同 token 后哈希相同。这不是从零重建容器，也不是对全游戏文本的覆盖。

字体 1 的旧通用变宽测量对双字节汉字使用旧扩展字度量；实际示例绘制为 12 像素而测量分别给出 7 或 0 像素。因而通用写回器仍拒绝此镜像。`make_card_album_sample.py` 是针对**一个固定条目**的独立封闭流程：核验输入、用户映射、overlay、指针、控制、容量、实际字体宽度，写全新副本，重算并核对目标扇区 EDC/ECC，然后比较整个镜像和 ID 647。它不会解除通用写回器的安全限制。

## 本地复现

下例在仓库根目录运行，所有输出目录须事先创建其父目录且目标自身不存在；不要把 `local/` 的内容提交或上传。脚本以精确 SHA-256 拒绝错误镜像；替换 ZIP 路径为本机实际路径。先前私有索引须来自 `local/dw3-probe/20261107T092435Z-03a090/`。

```bash
DW3_INPUT_ZIP='/Users/cayden0207/Downloads/数码宝贝世界3 欧版汉化.zip'
python3 tools/font_recovery_probe.py "$DW3_INPUT_ZIP" \
  local/dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json \
  local/dw3-probe/20261107T092435Z-03a090/SHARE_THIS_all_languages_audit.json \
  local/font-recovery/another-font-report.json
python3 tools/roundtrip_cn.py "$DW3_INPUT_ZIP" \
  local/dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json \
  local/font-recovery/another-roundtrip-report.json
python3 tools/make_card_album_sample.py "$DW3_INPUT_ZIP" \
  local/dw3-probe/20261107T092435Z-03a090/PRIVATE_DO_NOT_UPLOAD.json \
  local/dw3-probe/20261107T092435Z-03a090/SHARE_THIS_all_languages_audit.json \
  data/baselines/c7130c53-reviewed-glyphs.json data/p1_ui_draft.csv \
  local/font-recovery/another-card-sample
```

运行复测使用 [PCSX-Redux](https://github.com/pcsx-redux/pcsx-redux) `a74a0587` build 433、其随附 OpenBIOS SHA-1 `7bc681b350a3ac1e1e95ab27d56775384bcb4801`、参考项目 commit `841521ab2071096038316b93b8f778255b85730a` 及其 `psxstack` 子模块 `f93bf5905ac595aeba11762cb9e5fa9dc233987b`。本轮可复现脚本限制为已验证的 macOS ARM 模拟器可执行文件，SHA-256 为 `53451b6b313af0b5342d23ff50a60bea283a7268ef68df0f5482d8ade4e8cb3c`；其他配置仍可另行人工测试。`tools/replay_card_album.py --help` 列出本地路径参数；它读取参考项目的回放步骤，拒绝注入状态，创建全新的私人记忆卡和回放目录，截图仅存在该目录。回放流程包括新游戏、初战、新存档、重启读档、取得卡片盒、打开图鉴、返回、卡组界面和返回场景。自动检查点不代替目视检查；截图的中文身份由人工确认，不由像素差异或 OCR 推断。`collect_runtime_evidence.py` 可按 `--help` 指定本地日志和截图、经人工目视确认后生成不包含图片的分享报告。

实际初次捕获在进入图鉴后 90 帧仍为黑屏；等待 360 帧后得到有效画面。模拟器日志中有上游回放脚本为处理监听器泄漏而记录的 Lua 重置以及若干手柄命令警告；记录的流程仍完成，不能因此推断其他环境全部正常。
