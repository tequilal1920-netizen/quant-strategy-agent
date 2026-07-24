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

行业侧固定为31个申万一级行业、每行业8个互异专属业务字段、月度/周度Top10等权只做多，并以同频31行业等权为基准。风格侧在每个季度末将全部合格A股唯一标记为“大/中/小盘 × 成长/均衡/价值/红利”12个互斥且穷尽的风格箱，下一交易日执行Top3等权只做多，并以12风格箱等权为基准。训练、验证、测试严格分离，测试集只报告，不参与候选选择。