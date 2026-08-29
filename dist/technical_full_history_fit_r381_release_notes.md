# 技术分析 r38.1 全历史低频拟合发行说明

## 发行口径

本次发布将技术分析从“双模型”升级为“三轨模型”：

1. 模型一：纯技术信号栈，继续按训练、验证、封存测试治理。
2. 模型二：LLM 记忆多周期，继续按训练、验证、封存测试治理。
3. 模型三：全历史低频技术拟合，按最新需求不做训练/测试划分，全部成熟历史样本参与拟合和候选选择，并输出当前位置。

模型三是全样本回溯研究拟合版，不声明样本外验证，也不承诺未来收益。

## 模型三流程

- 信号来源：趋势动量、突破确认、回撤反转、量价确认、波动质量、防守择时六类纯 OHLCV 技术信号。
- 拟合范围：全部已成熟历史收益标签。
- 候选集合：综合技术、突破量价、防守趋势、回撤修复、集中低频、状态择时低频和各子信号。
- 交易节奏：四周或八周刷新排序，持仓缓冲，扣除换手成本。
- 选择目标：全样本夏普优先，同时惩罚过高换手、过深回撤和短样本。
- 护栏：候选至少 120 个有效周；短样本高夏普不采纳。

## 冻结结果

- 入选候选：全历史状态择时趋势低频。
- 股票池：全 A。
- 有效周数：224 周。
- 全历史年化收益：约 8.84%。
- 全历史夏普：约 0.53。
- 最大回撤：约 -26.59%。
- 周均换手：约 0.12。
- 当前信号日：2026-06-30。
- 当前位置输出：80 个候选，`technical-analysis current 数量=20` 可查询。

说明：出现过 1.51 的短样本夏普候选，但只有 3 个有效周，已被护栏排除。

## 网页与接口

- vNext 版本：`2026.08.17-technical-full-history-fit-vnext-r38.1`。
- 治理版本：`2026.08.17-technical-full-history-fit-governed-r38.1`。
- 技术分析页面诊断表新增“模型三：全历史低频拟合”。
- 最新截面表优先展示模型三当前位置、动作、目标权重和五维技术分位。
- 运行层新增：`python ai-models/technical-analysis/scripts/query.py current 数量=20`。

## 验证

本地通过：

```powershell
python -m pytest framework/backtest/test_technical_signal_model.py framework/backtest/test_kline_multiscale_expert.py framework/backtest/test_kline_supervised_ranker.py board/quant_strategy_agent_vnext/qa/test_kline_multiscale_evidence.py -q
```

结果：12 passed，63 warnings。warning 为既有依赖的 LightGBM/sklearn 特征名提醒。
