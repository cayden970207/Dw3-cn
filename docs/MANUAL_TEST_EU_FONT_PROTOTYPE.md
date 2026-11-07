# 欧版新字体四字样机：本机测试

这是已经生成的实验副本，不是可正式游玩的完整汉化。它暂借四个欧版日文字位；不要用来测试其他语言或覆盖已有存档。

1. 在模拟器配置中创建一份**独立记忆卡副本**，可以复制本机 `local/font-recovery/p1-batch03-fixture-v1/memcard1.mcd` 到新的私有文件，并将模拟器第一插槽指向副本。不要直接使用或覆盖个人存档。
2. 打开本机 `local/eu-original-font/prototype-v4-bank/DW2003-EU-font-prototype.cue`，选择英文，进入游戏内 Continue / 读档，选择独立卡上的存档。
3. 从旅馆下楼，经走廊回到卡片竞技场。打开状态菜单，选择 Card Album，观察左上角「卡片图鉴」四字。左右翻页至第 27 页、再回到第 1 页，退出并重新打开。
4. 如要复验存读档，在访问图鉴后回到旅馆并游戏内存档，完全退出模拟器，再从相同测试 CUE 冷启动、游戏内读档并重开图鉴。截图和记忆卡只保存在本机。

记录所用模拟器版本、图形设置、记忆卡副本和实际画面。这个步骤不能证明整套字库、其他窗口、日文或高清纹理替换已经可用。自动回放已在 PCSX-Redux/OpenBIOS 下完成相应范围，见 `reports/SHARE_THIS_eu_font_save_regression_report.json`。

如仅观察「槽」的字距，可分别启动本机 `local/eu-original-font/prototype-slot-visibility-v1/` 和 `local/eu-original-font/prototype-slot-spacing12-v1/` 中的测试 CUE，使用独立记忆卡副本，走同样路径进入图鉴。标题「记忆插槽」是临时观察文字；两个样机各自的存档冷读尚未回归，且实际记忆卡屏幕仍是原文。
