# 资产配置公开快照

`asset_allocation_snapshot.json` 由 `model/asset_allocation/build_snapshot.py` 离线生成。

- Web 请求只读该文件，不现场请求数据商。
- 仅 `quality.status=passed` 的快照可被 API 返回。
- 文件不得包含账号、口令、token、许可证或数据库连接串。
- `data_as_of.market` 与 `data_as_of.macro_complete` 分开标注，禁止用部分宏观月冒充完整月。
