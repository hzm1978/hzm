# 期货复盘训练系统：交易日修复包

[下载 futures_replay_fixed.zip](futures_replay_fixed.zip?raw=true)

修复 2026 年 10 月 31 日（周六）被错误生成独立日 K 的问题。这一段日 K 应为 10 月 30 日、11 月 2 日；周五夜盘与周六凌晨的分钟成交归入 11 月 2 日。

完全退出原程序，将 ZIP 中的 `futures_replay` 文件夹覆盖到原项目同名位置，再重启并重新加载行情。请包含新增的 `trading_calendar.py`。详细说明和回归测试在 ZIP 内。

10 项回归测试及原程序的 Qt 界面自检均通过。原上传文件未包含实际行情，因此未核验截图对应的实际 OHLC 数值。

ZIP SHA-256：`78eba8f0ceb1a22d28e045b4e9ec5365ffc99013fefede847c29972de939790b`
