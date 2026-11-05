# 合成数据绘图预览

两张图均由本包导出公式的AST及本地几何解释器生成，不是期货通客户端截图，也不是用户截图品种的真实行情。它们检查连线端点、价位范围、互斥和默认标签数量；实际客户端字体、缩放和速度仍需原生核对。

在项目根目录安装NumPy和Matplotlib后运行：

```text
python plot_audit.py --output docs/previews/synthetic_fall_rebound.png
python plot_audit.py --scenario trend_targets --output docs/previews/synthetic_trend_targets.png
```

第一张为长期下降后反弹；第二张以人工H1参数核对三条候选目标的几何与标签，不证明真实行情结构。
