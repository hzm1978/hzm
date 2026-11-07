# 1.4.1合成预览与明细

45个固定合成场景覆盖M/P/S、内部区间、两种分割及不同基准的目标价。图由导出公式AST在Matplotlib绘制，非客户端截图，非用户行情复原。

1.4.1新增s_auto_five，自动范围下保留3123→3351→3240→3591→3511→3620五浪，首浪H1=228，五浪后回调的分割量3123→3620。s_auto_five_down检查镜像；s_five_tail检查更多后续调整转折不会重新截掉早期两腿。s_leg_fib及其镜像在S4阶段只量紧前S3，即3240→3591；s_first_fib量首腿3123→3351；s_five_live没有回调时不画S尺。

synthetic_s_auto_five_EWT03.png、synthetic_s_leg_fib_EWT03.png、synthetic_s_five_tail_EWT03.png为安装版EWT03单层图，分别便于核对完整五浪、前一子浪分割及后续调整。对应JSON的S_fib.basis明确记录whole_group或preceding_subwave。数据只复现问题特征，不代表截图对应的实际逐根K线。

本版新增same_three、same_four检查第三同级Q的内部降级与第四Q重新配对；contract_equal允许七浪最后一对相等；recouple_equal检查内部同级对完成后回配原级。graded_expand、graded_contract用相邻合法、累计跨度越过大级别阈值的样本，防止把有效逐级七浪强制改为五浪。

white_same按用户白线的价格转折设计，前两段很小噪声不占满分级槽。当前外层6704→8036→7464→8770→8493→9846，Q跨度40/37，内部紫线保留8255、8093、9056。white_same_down为价格镜像，检查下跌对称性。合成K线的根数经过人为设计，不是截图对应行情的实际区间长度。

synthetic_white_same_EWT02.png单独展示安装版EWT02，便于区分P浪与内部调整。公共RV标签由EWT01输出，见完整white_same预览；该单层图不包含公共标签。

新增q_early检查早期横盘区间，q_merge检查合并原根及c之后重新计时，q_tiny排除仅影线和连续同向收盘的小区间，q_failed/q_failed_auto保留稍低末峰候选，q_reversal区分先上3浪再下3浪并锁住929.4高点，main_left/main_three保留前段，s_main展开最后P主浪，s_fib在S回调时量前一完整S推进段，target_bases解释2H1与4.236H1接近，guides_up检查有效支持线。

既有earlier_complete检查降级内部q仍入蓝色轮廓；p_whole外层按7/9同级Q计五浪，合并内部q仍保留，H1基准56.38，末实际高度倍数70/56.38。p_white检查较晚c，p_triangle检查e不同于极端a，p_s_correction检查最后P回调S，trend_targets的恒价输入不伪造S结构。main_owned另检查属于后续主浪的G转折不能改写前段1015.38起点。

同名JSON提供实际M/P/S归属、Q根数、RET%、实际H1倍数、分类、内部q、当前P反向结构、S尺和辅助线状态；price_plan的每个目标记录起算点。全部安装文件与完整模型的等价性、原生坐标另行核对。

CSV复查使用inspect_structure.py --csv candles.csv --output own.json --preview own.png，按时间升序的O/H/L/C。bar_index从0开始；字体和缩放仍需目标客户端界面核验。
