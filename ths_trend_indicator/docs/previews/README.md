# 1.1.4合成预览与明细

读取完整开发模型，含可选回调S；实际只加载EWT01/02时不显示较早历史层/可选S。图为Matplotlib预览，不是4.39客户端截图，也不是用户市场行情复原。安装等价与原生计算坐标另逐项核对。

重点样本：p_whole（超过八个细转折仍覆盖整段），p_white（较晚c和强势abc），p_triangle（e非最极端a），p_no_h1（仍有P），p_newtip（回调不等BN），p_s_correction（可选S归属）；另有short_inner_q（5、2、5），parent_correction、sideways、earlier_complete及原有六场景。

同名JSON的complete_P记录实际P起止、每个Q的起止/根数、调整末点、回调%、形态及细节限额。price_plan给实际取尺起止与方向；groups是保留的数学分级参考，不是所有组都被显示。所有合成场景由scenarios.py固定生成。

```text
python inspect_structure.py --scenario p_white --output structure.json --preview preview.png
python inspect_structure.py --csv candles.csv --output own_structure.json --preview own.png
```

CSV须按时间升序提供O/H/L/C；bar_index从0起。图例与形态编号见上级ASCII图例对照.md。实际字体/缩放仍需目标界面核验。
