# P1：从人工校对的简体中文到私有二进制替换清单

当前缺少用户原汉化镜像的真实中文字库对照；本工具只负责**在字形码表已经从原汉化中核实后**，把 Unicode 译文安全转换为替换字节清单。本仓库不提供未经核实的汉字编码假表。

## 第一步：实际完整文本扫描

```sh
python tools/scan_text_tables.py "/path/to/数码宝贝世界3 欧版汉化.zip" \
  --private-dump local/real-scan.json
```

此步骤需保证 `inventory_complete: true`。真实汉化可能修改原版文件布局，必须按实际游戏镜像适配，不得强行绕过结构错误。

## 第二步：完成字库对照（待完成的真实工程）

在 `local/glyph-profile.json` 中记录实际**每字对应的原版修改编码**（示例中全是合成字节，不能用于游戏）：

```json
{
  "schema_version": 1,
  "verified_for_image_sha256": "<游戏镜像的64位sha256>",
  "mapping_reviewed": true,
  "glyphs": {
    "甲": "ee",
    "乙": "ed"
  }
}
```

除汉字以外，真实译文所需的空格、全角标点、数字等字符都必须映射。没有对应字形的 Unicode 会立即失败；不能用近似编码猜测。控制码字节 `02` 保留专用，不允许映射成中文字形。可用码表支持 1–4 字节字形；多字节编码不可与短码重叠产生解析歧义。

`mapping_reviewed` 是**人工审核记录**，不是工具对渲染效果的保证。还须在游戏里验证实际字形、颜色、字宽和动态选项。

## 第三步：记录已经校对的中文译文

`local/reviewed.json` 格式（同样是演示 ID）：

```json
{
  "schema_version": 1,
  "source_image_sha256": "<与字库一致的sha256>",
  "entries": [
    {
      "file_id": 17, "table": null, "index": 1,
      "original_sha256": "<scan条目的raw_sha256>",
      "reviewed": true,
      "segments": ["甲", null, "乙"]
    }
  ]
}
```

`segments` 是被原游戏控制码隔开的**文字区域**。长度必须与当前原始条目中的区域数量完全一致；`null` 表示该段保留原始字节。包括说话人、动态人名和等待/换页控制码，都由工具复用原始字节，不允许重新插入或重排。例子有多少段取决于真实游戏条目，不是固定 3 段。每个替换必须等长或短于原原文的**编码字节容量**，暂不处理增加资源大小/指针重建。

## 第四步：生成私有二进制清单，再用于写回

```sh
python tools/prepare_translation_patch.py \
  local/real-scan.json local/glyph-profile.json local/reviewed.json \
  local/ready-to-apply.json

python tools/apply_text_bytes.py \
  "/path/to/数码宝贝世界3 欧版汉化.zip" \
  local/ready-to-apply.json local/dw2003-zh-new.bin
```

只有通过基础哈希、控制码一致性、字形覆盖、编码容量、原镜像数据完整性、EDC/ECC 与写回后文本重扫的改动才能生成测试副本。

**这不是发布补丁。** 还必须测量实际字宽、窗口裁剪和全部运行分支，并用模拟器验证菜单、对话、战斗与存读档。若只有已提交工具和合成数据测试，依然不能标记真实汉化补完完成。
