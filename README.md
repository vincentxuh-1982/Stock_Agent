# Stock Exchange Agent

一个面向指数、自选股、持仓股的本地股票分析 Agent MVP。

这个项目的目标不是输出“确定性买卖指令”，而是把每天重复的复盘、新闻整理、热点识别、仓位建议流程自动化，并保留清晰的分析依据。

## 当前能力

- 收盘后复盘指数和自选股，输出短期 `T+1~3` 与中期 `T+30~60` 观点
- 对复盘标的输出未来 3 个交易日上涨/下跌概率、预估波动和触发条件
- 计算均线、MACD、RSI、ATR、布林带、量能、区间位置等技术指标
- 扫描 RSS/Atom 新闻源，按主题关键词聚合市场热点
- 聚合财经快讯、个股新闻和市场人气榜，默认每两周生成一次市场洞察和下一热点候选
- 盘中读取自选股实时快照，叠加技术位输出实时关注优先级和当天剩余走势概率
- 针对自选股给出观察、试仓、等待回调等建仓建议
- 针对持仓股结合成本、现价、趋势、ATR 给出持有、减仓、止损、加仓观察建议
- 生成每日复盘与持仓简报，汇总指数、自选股/持仓股新闻、行业热点、持仓/建仓策略
- 交易时段每 30 分钟生成盘中实时策略简报：持仓股给操作策略，自选股只推建仓机会
- 支持微信推送通道：企业微信群机器人、Server 酱、PushPlus
- 支持 `synthetic` 演示数据、CSV 本地数据、AKShare A 股数据三种 provider
- 支持 A 股/创业板/港股混合股票池，标的类型包括 `a_stock`、`cn_index`、`hk_stock`、`hk_index`
- 支持常驻调度进程，在指定时间自动生成报告

## 快速开始

```bash
python3 -m stock_agent.cli review --config config/demo.json
python3 -m stock_agent.cli realtime --config config/demo.json
python3 -m stock_agent.cli news --config config/demo.json
python3 -m stock_agent.cli insights --config config/demo.json
python3 -m stock_agent.cli advise --config config/demo.json --portfolio data/portfolio.demo.json
python3 -m stock_agent.cli daily-digest --config config/demo.json --portfolio data/portfolio.demo.json
python3 -m stock_agent.cli realtime-push --config config/demo.json --portfolio data/portfolio.demo.json
```

报告默认输出到 `reports/`。

## 本地前端工作台

启动 Web 界面：

```bash
.venv/bin/python -m stock_agent.cli web --config config/local.json --portfolio data/portfolio.local.json
```

默认访问地址：

```text
http://127.0.0.1:8765
```

页面里可以切换实时、复盘、新闻、两周洞察、推荐/持仓建议。切换到页面后会自动抓取最新数据；实时页支持按秒自动刷新，最小间隔 5 秒。每个模块只展示当前报告，历史版本归档在 `reports/history/`。

右侧“标的查询”支持按代码或名称查询真实 A 股、港股和常用指数。查询结果可以直接加入自选股或指数池，并写回 `config/local.json`。

右侧“AI助手”会基于当前页面报告、自选股、指数和持仓上下文回答问题。默认使用本地报告规则，不会外传数据。需要接入 OpenAI 兼容接口时，在启动 Web 前设置：

```bash
export STOCK_AGENT_AI_PROVIDER=openai
export OPENAI_API_KEY="你的 API Key"
export OPENAI_MODEL="gpt-4o-mini"
```

外部 AI 模式会把当前页面报告和你的问题发送到配置的模型服务，请只在确认可接受该数据流向时开启。

两周洞察会专项抓取自选股个股新闻，并结合标的所在产业新闻、财经快讯和市场人气榜，输出下一热点候选。默认 14 天内重复运行会复用最近一份报告；需要强制刷新时：

```bash
.venv/bin/python -m stock_agent.cli insights --config config/local.json --force
```

## 打包成本地软件

生成 macOS 本地安装/更新包：

```bash
.venv/bin/python scripts/build_release.py --clean
```

输出在 `dist/`：

- `StockAgent-版本号-macos.zip`：完整安装包
- `StockAgent-版本号-update.zip`：后续更新包

解压后双击 `安装或更新.command`，程序会安装到：

```text
~/Applications/Stock Agent.app
```

用户数据不会放在 App 内，而是保存在：

```text
~/Library/Application Support/StockAgent/
```

其中包含 `config/local.json`、`data/portfolio.local.json` 和 `reports/`。因此后续更新只替换 App，不会覆盖自选股、持仓、交易流水和历史报告。详细说明见 `docs/packaging.md`。

安装或更新包会注册两个本地后台服务：

- `local.stock-agent.web`：常驻本地 Web 服务，登录后自动运行
- `local.stock-agent.scheduler`：定时生成报告和推送

也可以手动安装/刷新后台服务：

```bash
python3 scripts/install_launch_agents.py --app "$HOME/Applications/Stock Agent.app"
```

## 微信推送

在 `~/Library/Application Support/StockAgent/config/local.json` 或开发态 `config/local.json` 中配置 `push`：

```json
{
  "push": {
    "enabled": true,
    "provider": "wecom",
    "wechat_webhook_url": "企业微信群机器人 Webhook URL",
    "server_chan_send_key": "",
    "pushplus_token": "",
    "max_chars": 3500
  }
}
```

可选 provider：

- `wecom`：企业微信群机器人 Webhook
- `serverchan`：Server 酱 SendKey
- `pushplus`：PushPlus token

也可以用环境变量覆盖：`STOCK_AGENT_PUSH_PROVIDER`、`STOCK_AGENT_WECHAT_WEBHOOK_URL`、`STOCK_AGENT_SERVERCHAN_SEND_KEY`、`STOCK_AGENT_PUSHPLUS_TOKEN`。

## 接入真实 A 股数据

1. 安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

2. 复制并修改示例配置：

```bash
cp config/a_share.example.json config/local.json
```

3. 替换 `watchlist`、`portfolio`、`news_sources` 后运行：

```bash
python3 -m stock_agent.cli run-all --config config/local.json --portfolio data/portfolio.example.json
```

如果同时看 A 股、创业板和港股，可以从混合配置开始：

```bash
cp config/china_hk.example.json config/local.json
```

代码格式示例：

- A 股/创业板个股：`300750`，`kind` 使用 `a_stock`
- A 股指数：`399006`，`kind` 使用 `cn_index`
- 港股个股：`00700`，`kind` 使用 `hk_stock`
- 港股指数：`HSI`，`kind` 使用 `hk_index`

## 自动调度

常驻运行：

```bash
python3 -m stock_agent.cli schedule --config config/local.json --portfolio data/portfolio.example.json
```

默认调度时间在配置文件的 `schedules` 中，其中 `biweekly_insights` 会触发两周洞察生成；程序会自动复用 14 天内已有报告。更稳妥的生产方式是用 `launchd`、`cron`、服务器 systemd 或云函数定时触发 CLI。

## 风险边界

本项目仅做研究、复盘和辅助决策，不构成投资建议。概率预测来自本地量价规则模型，表示当前条件下的倾向，不是确定性预测。任何买卖都需要结合账户风险承受能力、交易纪律、市场流动性和最新公告自行判断。
