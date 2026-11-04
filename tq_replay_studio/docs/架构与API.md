# 程序结构与天勤接口

## 官方依据

本次阅读的文档为2026-11-04提供的最新版页面，软件依赖固定到实际验证过的SDK3.10.4。

| 用途 | 官方文档 |
|---|---|
| 总入口与快速开始 | https://doc.shinnytech.com/tqsdk/latest/index.html |
| 合约、行情与历史 | https://doc.shinnytech.com/tqsdk/latest/usage/mddatas.html |
| 方法和参数 | https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.api.html |
| 历史回测时钟 | https://doc.shinnytech.com/tqsdk/latest/usage/backtest.html |
| 账户及交易 | https://doc.shinnytech.com/tqsdk/latest/usage/trade.html |
| 支持的期货公司 | https://www.shinnytech.com/blog/tq-support-broker/ |
| 专业版数据功能 | https://doc.shinnytech.com/tqsdk/latest/profession.html |

行情概览页仍有8000根旧文字，API参考页和所固定SDK写的是10000；本程序按10000请求，也已实际取得10000根分钟数据。普通实时订阅、TqBacktest历史流、专业版批量下载是不同路径，没有把前者的10000根上限错误套到整个历史回放上。

## 分层

~~~text
Qt桌面
  main_window     复盘、多图、训练账户、存档、导入
  trade_dialog    实时模拟／实盘账户、委托核对、持仓和撤单
  history_dialog  交易日范围、历史接收进度及新训练
  chart           异步分析，价格绘制复用 technical/ui
       │ JSON标准输入输出 / QProcess
       ├── sdk_worker       TqApi行情、目录与按周期增量缓存
       ├── history_worker   TqBacktest历史流，逐批写入缓存
       └── trade_worker     TqSim / TqKq / TqAccount，账户和交易

SQLite WAL
  bars         按合约、周期、时间戳去重的市场缓存
  catalog      交易所合约信息
  sessions     每场训练的不可变行情快照及可变账户／位置／画线
  settings     显示偏好，不保存登录或资金密码
~~~

## API使用

- TqAuth：天勤／快期账号认证，认证进度回报桌面。
- query_quotes：六家交易所的 CONT、FUTURE，未到期目录。
- query_symbol_info：名称、合约乘数、价位及主连标的。
- get_quote：报价时间、盘口、实际主力合约及交易参数。
- get_kline_serial：实时原生周期，每序列最多请求10000根。
- is_serial_ready／wait_update：按实际到达情况推进，某周期未就绪不会阻止已就绪周期预览。
- TqBacktest：指定起止交易日，短滚动窗口逐步读取完整历史流，结束时处理 BacktestFinished。
- TqSim／TqKq：实时本地模拟及快期云端模拟账户。
- TqAccount：徽商等真实账户；没有在开发联机验证中构造真实资金账户。
- get_account／get_position／get_order／get_trade：账户状态和回报。
- insert_order／cancel_order：只在交易进程接收明确指令后调用；实盘前置解锁和确认令牌。

应用只用公开方法，不修改SDK认证权限、服务地址或内部行情缓冲区。SDK对象留在对应进程，传到桌面的是复制、校验过的普通数据。

## 数据与时间

统一使用北京时间。SDK纳秒戳先按UTC解析，再转Asia/Shanghai；夜盘按原生日历和交易日归属保存。夜盘日K可能标注下一个交易日的零点，即使标签晚于当前墙上时间也不能把这根真实进行中日K删除。

缓存按时间去重，增量通过每行指纹识别：断连恢复后窗口内的缺失多根、较早K线修正和末K变化都能写入，不只保存最后一根。SDK窗口之外且从未缓存的缺口需历史回放补充。

训练冻结行情和参数，进度只由操作推进。周／月复盘以原生日K作为基础逐日揭示，再聚合图形；四图副图也只聚合揭示前缀。过期异步读盘、旧品种回复和旧分析结果通过编号与内容指纹丢弃。

趋势计算使用线程池，行情／历史／交易使用独立进程；画图回调不直接联网。工作区备份用SQLite backup生成一致快照，包含缓存及训练，排除独立的登录凭据文件。

## 源码归属

technical下的图表及趋势规则、六组相应回归测试，来自用户仓库v8.4基础版本475635d。它们独立内置，便于沿用已经验证的书本规则、画线交互及空数组修复。数据、窗口、复盘、持久化、训练账户及交易进程为新实现。

依赖通过pip安装，不把TqSdk、Qt、pandas或书籍PDF复制入源码包。第三版书籍SHA-256：f7c93443bc5df0a2b849a718c817fe20b554bf86d7d4ea0af417b75f0fde16ea。
