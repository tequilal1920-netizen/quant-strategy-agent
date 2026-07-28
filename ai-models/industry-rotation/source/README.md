# 行业轮动

该目录只保留当前生产实现：

- `catalog.py`：31 个申万一级行业及字段目录。
- `engine.py`：时点数据、打分、组合和回测核心。
- `event_cache.py`：可复用事件缓存。
- `event_overrides.py`：行业事件补充规则。
- `build_snapshot.py`：正式轮动快照入口。
- `style_box_rotation.py`：季度股票级 3×4 风格箱与风格 Top3 轮动入口。
- `build_tracking.py`：正式跟踪快照入口。
- `test_contract.py`：生产快照合同验证。

正式输出写入：

- `board/quant_strategy_agent/data/rotation_snapshot.json`
- `board/quant_strategy_agent/data/rotation_tracking.json`

缓存和证据写入 `output/industry_rotation/`，数据库只从 `database/` 读取。观察日与可用日严格分离，测试集仅报告，不参与选择。

```powershell
python model\industry_rotation\build_snapshot.py
python model\industry_rotation\style_box_rotation.py
python model\industry_rotation\build_tracking.py
python model\industry_rotation\test_contract.py
```

行业侧固定为31个申万一级行业、每行业8个互异专属业务字段。月度和周度候选在预声明的Top10/Top5、等权/风险权重、持仓缓冲和现金预算结构内比较，并以同频31行业等权为基准。风格侧在每个季度末将全部合格A股唯一标记为“大/中/小盘 × 成长/均衡/价值/红利”12个互斥且穷尽的风格箱，下一交易日执行Top3等权只做多，并以12风格箱等权为基准。训练、验证、测试严格分离，测试集只报告或否决唯一挑战者，不参与候选排序。
## 回测复现与审计口径

- 设置 `INDUSTRY_ROTATION_SOURCE_XLSX` 指向含 `weekly`、`monthly`、`quarterly` 工作表的行业景气历史缓存，再运行隔离构建。
- 首次建仓换手为 100%。后续换手按 `0.5 × L1` 计算，`cost_rate` 为作用于单边换手的双边费率。
- 月频和周频候选只由训练集估计并由验证集定型。分年诊断和测试集收益只用于报告，不参与选型。
- `return_loss_diagnostics` 用于识别失效年份和相对基准的收益损失，不允许据此回填测试期参数。
- 生产快照替换前必须通过 31 行业、每行业 8 个 live 专属业务字段以及 PIT 可用日门禁。
- v4.7 先将同一频率的全部候选裁剪到共同可评估起点，再由训练期和验证期选出唯一研究挑战者。封存测试只允许否决该挑战者相对预声明 C6 冠军的晋级，不参与候选排序或参数调整。
- C22 研报增强动量候选使用滞后 11 至 5 个月周度超额收益均值/波动率，并按当月超额收益历史分位进行方向相关的反转修正；景气度和拥挤度采用连续软组合，不设置事后硬阈值。
- 未通过晋级门的候选保留在 `output/model_improvement/` 作为否定性研究证据，不得替换生产快照。
