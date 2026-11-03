# 期货复盘训练系统 v5：独立子浪、区间与回调位置

[直接下载完整修复包 futures_replay_fixed_v5.zip](https://github.com/hzm1978/hzm/raw/refs/heads/fix/futures-replay-trading-days/futures_replay_fixed_v5.zip)

依据最新版 **3.pdf**（《趋势交易法》第三版，共731页）重写趋势指标核心，通读全书文字并核对关键规则图示。包括区间跨度与级别、波浪线、趋势/拐点线、主A/导A、标准K与关键位置反转、第三版四类调整浪、回调%、延时突破及PSTD参考。

v5 修正主趋势方向掩盖反向子浪的问题：子结构独立按Q计数，默认显示子1–5/7、子q根数、子2/4/6回调百分比、子A及子趋势/拐点线。第5浪运行中与未破浪3的候选分开标记；回调参考尺跟随当前子浪，不沿用父浪旧价位。

调整浪按所属浪段识别，取消连续滑窗重复拼接abcde；历史/导航解释保留在 **指标明细**。40%、62%、78.6%参考价用独立小表格显示，减少小窗口的标注碰撞。

## 使用

退出旧程序并备份，解压后用整个 `futures_replay` 文件夹覆盖原同名文件夹，务必包含新增的 `trend_hierarchy.py`。保留原行情与交易记录。重启后勾选 **趋势交易**，点击 **指标明细**。独立旧“波浪”工具不必同时勾选。“独立子浪计数”默认开启，不需要开启“更多导航区间”。

源码也直接保存在本仓库，可使用Python启动：

```text
python -m pip install -r requirements.txt
python run.py
```

这是完整Python源码包，不是独立exe安装程序。已有运行入口仍可使用。

## 规则与验证

- [指标规则、原书页码与程序参数](指标规则说明.md)
- [修复及安装说明](修复说明.md)
- [验证记录](验证记录.md)
- [v5 子浪效果示意](trend_indicator_v5_preview.png)（构造行情，非实际SN报价）

74项回归测试在pandas 2.2.3和3.1.0下均通过，原Qt自检及真实主窗口验证通过。新增反向五子浪、未收盘/未来隔离、开放Q、局部内外区间、回调公式、调整浪归属及553像素小窗口实际标注检查。主窗口验证覆盖指标按钮、设置保存恢复、日/周/月图10.30 → 11.2 → 10.30的实际加减。数据更新只读数组及31日假日K修复继续保留。

复杂结构保留候选、分级及确认时刻；后续数据可触发合法重划。形态、价格目标与周期估计供复盘核对。尚未使用用户实际SN行情或在线账户验证。

压缩包文件数、大小与SHA-256见仓库中的 [v5_manifest.json](https://github.com/hzm1978/hzm/blob/fix/futures-replay-trading-days/v5_manifest.json)。

原书：[book Release](https://github.com/hzm1978/hzm/releases/tag/book) · [最新版3.pdf](https://github.com/hzm1978/hzm/releases/download/book/3.pdf)。

旧版压缩包继续保留。
