<p align="center">
  <img src="assets/logo.png" width="112" alt="US Stock Crash Monitor logo">
</p>

<h1 align="center">US Stock Crash Monitor</h1>

<p align="center">用六因子视角观察美股风险，让评分、数据来源与主要驱动一目了然。</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square" alt="Python 3.10 or later">
  <img src="https://img.shields.io/badge/Streamlit-1.52%2B-FF4B4B?style=flat-square" alt="Streamlit 1.52 or later">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-65C6B0?style=flat-square" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Version-1.4-243844?style=flat-square" alt="Version 1.4">
</p>

<p align="center"><b>简体中文</b> · <a href="README_EN.md">English</a> · <a href="docs/UI_REDESIGN.md">界面设计说明</a> · <a href="docs/FEATURE_OPTIONS.md">功能路线</a></p>


## 项目简介

**US Stock Crash Monitor** 将估值、信用、美债、技术面与市场情绪放在同一个研究面板中，帮助你观察标普500的风险信号，追踪变化并查看主要驱动因素。

项目采用 **Python + Streamlit + Plotly**，强调可解释性：每个读数来自哪里、哪些因子参与评分、权重如何归一化、共振和红线如何改变总分，都能在面板中核对。

**当前版本：v1.4 · 2026-10-10 · 浅色界面 / VOO** ｜ [English](README_EN.md) ｜ [项目优化记录](docs/2026-10-10-project-review.md) ｜ [待选新增功能](docs/FEATURE_OPTIONS.md)

评分衡量规则模型中的风险信号，不是未来崩盘概率。历史压力对比使用近似情景参数，不是回测。

## 核心功能

- **六因子监测**：巴菲特指标、席勒市盈率、信用利差、美债利差、均线乖离、恐慌贪婪。
- **可解释风险评分**：权重归一化、信用历史分位数、共振警报、红线托底与贡献图。
- **VOO行情分析**：标普500ETF复权日线、均线和成交量，独立缓存行情与宏观数据。
- **明确的数据状态**：缺失项保持缺失，手动覆盖、演示行情与参考评分分别标注。
- **本地历史趋势**：按行情日期、模型版本、权重与评分口径保存，采用原子写入。
- **历史情景对比**：使用当前权重查看近似压力情景的反应，支持理解模型规则。

## 界面

默认采用**浅色金融研究面板**，固定监测 **VOO（标普500ETF）**。三块摘要展示风险、数据覆盖率和主要驱动；左侧价格、右侧贡献与下方趋势／解读沿用参考图布局，统一浅色样式。权重、连接和手动校准收进顶部模型设置，因子和数据来源有独立页面。

![VOO浅色界面示例——测试数据](main.png)

截图使用明确标注的测试数据展示界面。实际来源受限时，应用会显示缺失、演示模式和参考评分。还可以打开 [可交互布局示意](docs/ui-proposal.html) 试用示例权重与图表范围，该示意全部为模拟数据。

## 快速开始

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/middletoo/US_Stock_Crash_Monitor.git
cd US_Stock_Crash_Monitor
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

在项目目录中运行后，访问 [本地面板](http://localhost:8501)。

可选：复制 `.env.example` 为 `.env`，填入 `FRED_API_KEY`。密钥用于获取高收益债利差及历史序列、美债备用数据；不配置时信用因子保持缺失，可手动输入或将权重设为零。应用从自身目录读取 `.env`，文件已被 Git 忽略。

## v1.4 优化

- 缺失指标保持缺失；预设值只用于手动输入的起始值，不会自动参与评分。
- 权重为零的因子退出加权、共振与红线规则；全部设为零时恢复默认权重。
- 评分、实际权重和贡献图使用同一份模型结果，贡献图显示红线、共振与封顶调整。
- VOO行情与宏观数据分别缓存30分钟，独立宏观来源并发请求。
- 关闭自动获取后只下载 ETF 行情；10Y 缺失时独立执行 CNBC → 东方财富 → FRED 降级。
- 东方财富只读取最新 20 条数据，移除只用于全量历史下载的 AkShare 依赖。
- 恢复 TLS 证书验证；代理按调用上下文隔离，清空代理恢复直连，不修改进程代理环境变量。
- 信用利差历史过滤非法值、兼容数组，重复观测采用中位秩，显示实际历史样本数。
- 日线少于 200 个有效交易日时不生成无效均线；演示行情固定随机种子，OHLC 与成交量保持一致。
- 历史 CSV 按行情日期、标的、模型版本与权重配置隔离；使用线程/进程锁和原子写入。旧三列记录保留，但不混入新版趋势。

## 数据源

| 指标 | 主来源 | 失败处理 |
|---|---|---|
| ETF 复权日线 / 200 日均线 | yfinance | 标记为演示行情；不计入技术评分或正式历史 |
| 10Y 收益率 | yfinance `^TNX` | CNBC → 东方财富 → FRED → 缺失/手动 |
| 2Y 收益率 | CNBC | 东方财富 → FRED → 缺失/手动 |
| 高收益债信用利差 | FRED `BAMLH0A0HYM2`，需要 key | 缺失/手动；有足够历史时用分位数 |
| 巴菲特指标 | GuruFocus | 缺失/手动 |
| 席勒市盈率 | Multpl | 缺失/手动 |
| 恐慌贪婪指数 | CNN dataviz | 缺失/手动 |

GuruFocus 失败时，可用 FRED `NCBEILQ027S` 与同季度 GDP 计算**非金融公司权益/GDP 代理比值**，只作为补充展示。该序列并非全部上市公司总市值，不使用旧版固定比例校准，也不自动纳入巴菲特评分。[FRED 序列说明](https://fred.stlouisfed.org/series/NCBEILQ027S)

第三方站点可能限流、拒绝访问或更改结构。数据源状态代表获取结果，不保证所有读数都为同一时刻；行情为复权日线，不是盘中实时报价。

## 使用与记录规则

1. 打开顶部“模型设置”调整因子权重；无需使总和等于 100%。
2. 每个指标可以勾选“手动校准”。失败项不勾选时保持缺失，自动恢复后重新使用自动值。
3. 缺失因子退出计算，可用因子重新归一化；页面显示原始权重覆盖率，并标注为参考评分。
4. 仅行情真实、已启用因子完整且没有手动覆盖时写入正式历史。手动、缺失和演示数据不会覆盖自动记录。
5. 同一行情日期与评分配置只保留最新评分，周末不会按启动日期新增一个交易日。不同模型、权重和信用评分口径分别展示趋势。
6. “刷新数据”仅清除本项目的行情与宏观缓存。重启后历史 CSV 保留。

默认权重：巴菲特 15%、席勒 20%、信用 20%、美债利差 20%、均线乖离 15%、情绪 10%。

风险区间统一为 `<60` 较低评分、`60–<80` 风险累积、`≥80` 高风险。信用历史采用实际可得的近 5 年观测；其他因子仍采用经验阈值。

## 项目结构

| 文件 | 职责 |
|---|---|
| `app.py` | 侧边栏配置、缓存、数据质量提示、面板渲染 |
| `data_fetcher.py` | 外部请求、代理隔离、并发与备用链 |
| `risk_model.py` | 评分、权重归一化、覆盖率、贡献、近似历史情景 |
| `risk_history.py` | 历史配置标识、锁、原子写入与旧文件兼容 |
| `charts.py` / `ui_theme.py` | 行情图与统一浅色图表样式 |
| `tests/` | 模型、数据源、历史持久化和面板交互回归测试 |

## 开发验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
```

检查真实数据源（从本地 `.env` 读取 key，不把 key 放在命令行参数中）：

```powershell
python data_fetcher.py
```

## 模型边界

- 均线乖离和情绪评分偏向识别过热，不完整覆盖正在发生的下跌；深度负乖离得低分，不能据此推断市场安全。
- 正利差的小幅平坦区间仍采用原有经验分数；当前快照无法证明此前曾倒挂。
- 当前压力对比统一使用信用绝对阈值，可能与主仪表盘的历史分位数评分不同。
- 监测标的固定为VOO；估值、信用与情绪反映全市场背景，不是ETF独有指标。
- 历史只能在应用被打开且数据完整时积累，暂未增加后台采集或通知。

[待选功能清单](docs/FEATURE_OPTIONS.md) 给出了回测、双风险评分、定时监测和数据源容灾等扩展方案，选择后再实现。

本项目用于编程学习与量化研究，不构成投资建议。许可证：[MIT](LICENSE)。

## 路线与贡献

下一步可选择真实回测、下跌压力评分、定时采集、收益率曲线状态、数据源容灾或研究导出。这些为待选方案，尚未实现，详见 [功能清单](docs/FEATURE_OPTIONS.md) 与 [界面设计说明](docs/UI_REDESIGN.md)。

欢迎提交问题或改进。报告问题时附上复现步骤、预期与实际行为、依赖版本；提交代码前运行测试与静态检查。请勿将密钥写入Issue、日志或提交。


提交时请使用GitHub提供的 `noreply` 匿名邮箱，并在 [GitHub邮件设置](https://github.com/settings/emails) 开启邮箱隐私和阻止暴露私人邮箱的推送。

## 品牌资源

Logo将雷达环与市场折线结合，表达持续观察市场信号。图像位于 [assets/logo.png](assets/logo.png)，设计提示词和使用说明见 [品牌文档](assets/BRAND.md)。
