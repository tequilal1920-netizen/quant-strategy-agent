# SHH Skill

## 触发

当任务涉及远程电脑、WarrenQ 研报搜索、公网连接、公众号或网页登录态、Tailscale Funnel、生产目录覆盖、远程计划任务、远程浏览器会话时使用。

## 参考

本机参考目录为 `C:\Users\Rye\Desktop\Program\ELSE\SHH`。执行前先确认远程连接、目标主机、端口、生产目录和任务名，避免误连当前电脑或错误目录。

## 纪律

1. 研报搜索优先读取已登录远程浏览器或可持续接口，最终链接应尽量指向 PDF/附件本身。
2. 公网修复先查 `/healthz`、Funnel、监听端口和计划任务，再改代码。
3. 远程覆盖前必须备份精确文件清单；覆盖后重启并验证公网。
4. 不把登录账号、token、cookie、refresh token 写入源码、README、日志或 GitHub。
