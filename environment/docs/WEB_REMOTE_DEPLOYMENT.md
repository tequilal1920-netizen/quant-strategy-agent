# Web and Remote Deployment Boundary

网页源码统一放在 board/，模型源码统一放在 model/；禁止重新创建旧的 web/、models/ 或版本号副本目录。

## 当前正式入口

- 统一量化看板：board/quant_strategy_agent/main.py
- 数据看板服务：board/public_dashboard/app.py
- 部署与验证脚本：environment/deployment/
- 运行结果与发布包：output/（不提交 Git）
- 数据库：database/（不提交 Git、不复制到发布包）

## 远程部署原则

- 使用 SSH/Tailscale 连接已授权服务器。
- 新版本先上传到独立目录和备用端口。
- 先做版本、登录、核心 API、响应体积和缓存验证，再切换正式任务。
- 切换前保存计划任务 XML 与被替换源码；失败自动回滚。
- 远端数据库继续使用原文件，只通过服务器私有环境变量传递路径。
- 任何账号、口令、token、许可证和模型密钥都只存在于服务器私有 env 或系统凭据存储。

## 性能边界

- HTTP 请求只读取冻结快照，不在页面跳转时重新训练模型。
- 大 JSON 支持 gzip、ETag、条件请求和短期缓存。
- 数据看板首屏使用 metadata 视图，时序点按需批量加载。
- 静态资源使用带 ETag 的长期 immutable 缓存。

## 禁止事项

- 不覆盖未纳入本项目的既有站点。
- 不在浏览器中保存或返回服务端凭据。
- 不把 notebook、数据库构建或模型训练放到网页请求线程。
- 不在正式目录保留带版本号或 final_final 等中间版本文件。