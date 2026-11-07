# 工具测试记录 — 2026-11-07

## 可核验结果

- 已测试 commit：`3f3a4349ae0a60fcc5ab24a5a2e41ce1d4c980bc`。
- Workflow：Tooling QA (synthetic fixtures only)。
- Run：42345069530，状态 completed，结论 success。
- 运行记录：https://github.com/cayden970207/Dw3-cn/actions/runs/42345069530
- Python 3.11 job：128320934681，success；读取日志确认 `Ran 35 tests` / `OK`。
- Python 3.12 job：128320934454，success；编译、单元测试和示例检查步骤均 success。

本记录来源于实际 GitHub Actions 返回的状态和日志，不是本地执行推测。

## 通过的范围

只读镜像指纹工具在人工构造文件上的哈希、格式候选、启动编号候选、拒绝压缩文件、期望哈希退出码及不修改输入行为；文本检查器在合成配置上的控制码、缺字、宽度、行数、字节预算、重复编号、非法字符及 CLI 行为。

示例目录输出 `status: pending`，原因是示例字形配置、未核实真实尺寸和未复核译文。这是预期结果，不代表游戏内容通过。脚本报告始终保持 `gameplay_verified: false`。

## 未验证的范围

真实原镜像 SHA-256、原字库映射、实际汉化文本、历史回写代码、全量翻译、镜像重新打包、模拟器运行、画面排版、剧情分支和存读档。当前没有新增可玩的汉化补丁。

本地执行环境仍不可用；GitHub CI 只运行合成数据，没有向公开仓库或 CI 上传游戏镜像、原字库和个人存档。

Actions 日志存在运行时依赖的 Node.js 20 弃用警告；本次任务仍成功。该警告不属于游戏显示测试，也不将本次结果描述为“零警告”。
