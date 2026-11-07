# 文本检查器格式 v1

本格式是本项目新建的中间格式，不是 DW3 原生格式。目前没有实际镜像解码器或编码表适配器。

## 字形配置

见 `data/profile.example.json`。`schema_version` 为 1；`synthetic` 必须显式填写。`glyphs` 将单个 Unicode 字符映射到非空十六进制编码和非负 `advance_px`。字形编码含 NUL 时拒绝。`terminator_hex` 纳入每条文本的字节预算。

`controls` 以完整数字标记（如 `<5,1>`）为键，各有 `hex`、`kind`。支持 `newline`、`page`、`wait`、`insert`、`other`。`insert` 必须给出实际动态内容的保守最大 `max_width_px`；不得拿英文平均宽度代替中文边界值。

所有示例编码、字宽、容量只是人工测试数据，不能回写游戏。`measurement_verified` 只是待复核的元数据声明，不会让工具自动证明配置真实。

## 条目

每条包含唯一 `id`、字符串 `source` 和 `target`、`state`、`limits`。`limits` 必须有正整数 `max_width_px`、`max_lines`（每页）、`max_bytes`（含终止符）。来源坐标、原始字节哈希和实际窗口应由后续导出器提供，不从示例猜测。

状态为 `draft`、`translated`、`reviewed`、`game_verified`。最后一种要求 `runtime_evidence` 引用，但检查器不验证该引用的真实性，不因此声称运行通过。

目前保守要求源文和译文的**全部控制码及参数顺序完全一致**。重排换行或新增分页需要另行工程审核，不能直接静默放行。角括号用于控制码，字面角括号暂不支持；原始换行、tab、NUL 和不可见控制字符禁止，换行使用明确标记。

## 输出与退出码

- 0：没有静态错误；默认仍可能有 pending 项。
- 1：有静态错误，或 `--strict` 下存在 pending。
- 2：文件读取、JSON 或配置格式错误。

JSON 报告有 `errors`、`pending`、`measurements`、`status` 和始终为 false 的 `gameplay_verified`。缺字时测量值不完整，相关错误必须先解决，不能使用该条宽度作为可靠证明。

## 不在检查器能力内

未提供的文本、实际字形轮廓/字间叠压、字体渲染器差异、动态缓冲区、选项逻辑、资源/指针完整性、存读档、全流程覆盖、翻译准确度、剩余英文和截图验收。CI 通过也不能宣称这些通过。
