# 004 Ubuntu 24.04 可重复生产部署

**优先级** P0 上线门禁
**审查结论** 已确认，静态审查；目标主机尚未验证。
**发布状态** 待批准

## 问题背景

当前部署由 Windows 启动脚本、宿主机 Uvicorn 和局部 Compose 组成。Ubuntu 需要完整、可维护的服务拓扑。

## 实际代码依据

Compose 仅含 MySQL、Redis、MinIO 初始化/服务、Nginx；Nginx 走宿主机网关地址，PowerShell 绑定环回口。Worker / Beat 需手工启动，应用仅暴露 MySQL/Redis 聚合健康检查。[Compose](../../../docker-compose.yml#L9) · [Nginx](../../../nginx/nginx.conf#L36) · [脚本](../../../scripts/start_prod.ps1#L82) · [健康检查](../../../backend/app/main.py#L161)

## 风险说明

容器网络无法依赖宿主环回启动方式；机器重启后 API、消费者和恢复扫描器可能缺席。网关存活无法证明系统可接单。

## 改造目标

新增 Ubuntu 部署入口、服务依赖、端口边界、就绪探测及版本化更新/回滚。用隔离的 Ubuntu 24.04 验证。

## 涉及文件

新增 `deploy/ubuntu/` 和生产 Compose、容器构建文件、生产 Nginx 配置与运维说明；按需接入应用健康路由。

## 修改范围

由 Compose 管理 API、生成、转码、Beat、Dispatcher 及依赖。配置内部 DNS、TLS、最小暴露、自动重启、优雅关闭、资源和日志上限、健康检查。保留本机 Windows 启动路径。接入 005 备份、恢复与迁移步骤。

## 明确不修改的内容

不部署真实主机，不覆盖现有部署配置，不重建现有数据卷或更换中间件。交付阶段不默认采用高并发。

## 数据库影响

不在部署脚本中直接调整历史数据；迁移由显式版本步骤执行，重复部署保留卷和已有账号。

## 兼容性要求

保持 Vue 路由、HTTP API、签名下载与 Range。旧 Windows 脚本继续供本机使用。

## 测试要求

在隔离 Ubuntu 24.04 校验 Compose 配置、首次与重复部署、TLS、端口范围、容器退出和宿主重启、旧版回滚、端到端冒烟。

## 验收标准

- [ ] 重跑部署后已有作品、数据库和对象保持可用。
- [ ] 公开入口仅开放批准端口，服务探测能区分未就绪。
- [ ] 宿主重启后必需组件拉起，恢复行为由 010 验证。

## 回滚方案

固定镜像摘要并保留上一版 Compose。暂停接单，依据迁移兼容矩阵切回旧组件，继续使用现有卷，禁止通过销毁卷回滚。

## 依赖的其他 Issue

最终验收依赖 [003](003-production-security.md)、[005](005-mysql-durability.md)、[006](006-worker-resource-isolation.md)、[007](007-worker-recovery.md)、[008](008-minio-consistency.md)。配置设计可提前进行。
