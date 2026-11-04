# 全新：天勤复盘工作台 1.0.1

[下载完整独立源码 ZIP](https://github.com/hzm1978/hzm/raw/refs/heads/feat/tq-replay-studio/tq_replay_studio_v1_0_1_source.zip)

[仅修复启动的覆盖补丁](https://github.com/hzm1978/hzm/raw/refs/heads/feat/tq-replay-studio/tq_replay_studio_v1_0_1_startup_patch.zip)

[源码目录与完整功能说明](tq_replay_studio/README.md) · [官方API与架构](tq_replay_studio/docs/架构与API.md) · [交易接入说明](tq_replay_studio/docs/交易接入说明.md) · [验证记录](tq_replay_studio/docs/验证记录.md)

以官方天勤API开发的独立桌面工程，保留历史训练、多周期图、播放、时间轴、手动画线、第三版趋势指标、模拟交易、统计及存档；增加按日期从天勤载入历史、真实实时行情模拟，以及徽商期货TqAccount接入。

- 新工程放在tq_replay_studio，不覆盖此前v8.4程序。
- 解压后直接运行启动工作台.bat，首次自动准备兼容Python及依赖。
- Windows资金账号和交易密码在本机输入，源码没有真实凭据。
- 实盘通道已实现但未连接用户真实资金账号；模拟网关及真实Qt检查默认锁定、确认、撤单和错误处理。
- 官方真实数据核对：JM当日分钟／日线、沪银2016年1月5—6日1110根分钟历史、原生TqSim历史交易时钟下开仓／平今。

预览：[双图复盘](tq_replay_studio/previews/studio_two.png) · [四图](tq_replay_studio/previews/studio_four.png) · [统计](tq_replay_studio/previews/statistics.png) · [徽商设置（未登录）](tq_replay_studio/previews/broker_setup.png)

包大小、根数和校验见同目录tq_replay_studio_v1_0_1_manifest.json。
