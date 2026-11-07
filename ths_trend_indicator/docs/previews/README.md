# 1.3.0合成预览与明细

30个固定合成场景覆盖M/P/S、内部区间、两种分割及不同基准的目标价。图由导出公式AST在Matplotlib绘制，非客户端截图，非用户行情复原。

新增q_early检查早期横盘区间，q_merge检查合并原根及c之后重新计时，q_tiny排除仅影线和连续同向收盘的小区间，q_failed/q_failed_auto保留稍低末峰候选，q_reversal区分先上3浪再下3浪并锁住929.4高点，main_left/main_three保留前段，s_main展开最后P主浪，s_fib在S回调时量前一完整S推进段，target_bases解释2H1与4.236H1接近，guides_up检查有效支持线。

既有earlier_complete检查降级内部q仍入蓝色轮廓；p_whole的数量由当前Q合并结果决定，不强制保留旧版7浪结论。p_white检查较晚c，p_triangle检查e不同于极端a，p_s_correction检查最后P回调S，trend_targets的恒价输入不伪造S结构。

同名JSON提供实际M/P/S归属、Q根数、RET%、实际H1倍数、分类、内部q、当前P反向结构、S尺和辅助线状态；price_plan的每个目标记录起算点。全部安装文件与完整模型的等价性、原生坐标另行核对。

CSV复查使用inspect_structure.py --csv candles.csv --output own.json --preview own.png，按时间升序的O/H/L/C。bar_index从0开始；字体和缩放仍需目标客户端界面核验。
