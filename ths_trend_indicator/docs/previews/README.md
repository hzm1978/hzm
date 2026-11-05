# 合成预览与明细

1.1.3图片读取完整开发模型EWTN_ASCII.txt，三个安装图层合计的线段、文字/数值和位置另逐项对照。原生计算坐标的独立比较见上级目录的原生计算核对.json，原始输出见原生计算样本.json。K线均由scenarios.py生成，不代表真实行情或期货通原生渲染。同名JSON保留源码哈希、结构、所属范围、Q起止/长度、回调、当前A及价格尺状态。图上ASCII简称见上级目录的ASCII图例对照.md。

| 场景 | 核对重点 |
|---|---|
| fall_rebound | 长下降合并主三浪及内部子五浪、35/35的Q、前完整段七条分割 |
| five_up | 自动上涨五浪和各自回调 |
| nested_five_down | 工作方向尚未充分确认时的下降五子浪 |
| seven_expanding | 自动扩张七浪、三组Q |
| triangle_e | 自动收缩三角，e=175高于a=159，外起点201，A收盘确认 |
| trend_targets | 人工H1的三个最近未触及目标，比例/价格在线上 |

在本目录运行（NumPy，图片另需Matplotlib）：

```text
python inspect_structure.py --scenario fall_rebound --output docs/previews/synthetic_fall_rebound.json --preview docs/previews/synthetic_fall_rebound.png
python inspect_structure.py --scenario triangle_e --output docs/previews/synthetic_triangle_e.json --preview docs/previews/synthetic_triangle_e.png
```

没有额外隐藏标签。横轴从0起表示实际K线序号，字体与缩放仍须原生核对。
