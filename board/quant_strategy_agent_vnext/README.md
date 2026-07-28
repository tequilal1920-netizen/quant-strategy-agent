# 统一量化策略看板

`main.py` 是唯一正式应用入口。它加载基础看板、行业轮动和因子实验室蓝图，并为九个一级模块提供统一登录、状态、路由、压缩和缓存策略。

## 运行

在本目录中：

```powershell
python -m pip install -r requirements.txt
$env:QUANT_AGENT_USER = "local-user"
$env:QUANT_AGENT_PASSWORD = "local-password"
$env:QUANT_AGENT_SECRET = "replace-with-random-secret"
python -m waitress --host=127.0.0.1 --port=8071 main:app
```

生产启动脚本：`deploy/run_service.ps1`。

## 数据与性能

- 页面只按需读取 `data/` 中的正式快照，不在点击时运行模型。
- `/api/services` 并发检查上游服务并缓存 60 秒。
- JSON、文本和静态资源支持 gzip；快照支持 ETag 和条件请求 304。
- 前端只保留 8 个页面视图缓存，淘汰时释放 Plotly 图形。
- 所有一级与二级导航由 `static/js/app.js` 的单一分发器管理。

## UI 合同

`static/css/ui_unified.css` 最后加载并统一字体、字号、卡片、图表和移动端布局。绿色表示正常，蓝色表示运行/加载，红色表示问题。

## 验证

```powershell
python qa/test_canonical_app.py
```

浏览器回归必须覆盖模板中的全部 `data-target`，并检查目标标题、激活状态、控制台、空白内容、重复点击和横向溢出。
