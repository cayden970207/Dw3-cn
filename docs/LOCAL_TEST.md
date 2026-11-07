# 一次本地诊断：请用户协助（真正欧版汉化镜像）

目前 ChatGPT 的本地执行环境持续在打开大型游戏 ZIP 时返回 ClientError，公开 GitHub 仓库并不包含你的 ISO。因此我们需要一次**在你自己的电脑本地运行**的诊断，以便准确继续汉化。

只需要 Python 3.11+，不安装额外 Python 包。

## macOS / Linux

```sh
git clone https://github.com/cayden970207/Dw3-cn.git
cd Dw3-cn
python3 tools/local_probe.py "/绝对路径/数码宝贝世界3 欧版汉化.zip"
```

如果之前已经 clone，进入项目后运行 `git pull` 即可。

## Windows PowerShell

```powershell
git clone https://github.com/cayden970207/Dw3-cn.git
cd Dw3-cn
py -3 tools/local_probe.py "C:\你的文件目录\数码宝贝世界3 欧版汉化.zip"
```

也可以直接把 ZIP 拖进终端自动填写绝对路径。执行时应保留足够的临时磁盘空间；工具需要解压约一张光盘镜像至系统临时目录进行只读扫描。

## 需要回传的内容

程序会打印类似：

```text
SHARE THIS SMALL REPORT ONLY: local/dw3-probe/2026.../SHARE_THIS_report.json
Status code: 0
```

**只需将 `SHARE_THIS_report.json` 上传回 ChatGPT。** 即使状态码为 1 或 2，也请上传该报告；错误阶段信息可帮助定位欧版汉化改过哪些结构。

同一目录还有 `PRIVATE_DO_NOT_UPLOAD.json`，它包含原始游戏文本字节，**请留在自己电脑，不要上传到公开 GitHub**。游戏 ZIP、ISO、BIOS 及解包内容也不需要上传到 GitHub。

程序不会修改你的原 ZIP，不会向外传任何原始游戏数据，报告只有镜像指纹、50 类参考文本资源的结构与条目数量。结构检测成功依然不表示中文正确或游戏通关；下一阶段才是恢复汉字映射、真实漏译、首次安全写回与游戏内测试。

## 失败处理

- `fingerprint` 失败：ZIP 内可能不是单个 ISO/BIN/IMG，或压缩包损坏；如果有多个成员可用 `--member "完整ZIP成员名"`。
- `text_scan` 失败：欧版汉化可能修改过 ISO 路径、EXE 文件表或文本容器。**不能绕过检查**；根据报告修扫描器后再继续。
- 输出 `structure_incomplete`：已有部分结构结果，仍未达到安全写回前提；请把报告回传。

额外说明：诊断不是游戏试玩，也不产生可玩补丁。只有取得精确的真实镜像布局、中文字库映射之后才会需要你进行游戏画面的选项与存读档测试。
