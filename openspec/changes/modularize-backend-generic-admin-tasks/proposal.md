# 通用任务与管理员任务六方法模块化草案

## Why

固定旧后端的通用任务创建、更新、进度、外部状态和管理员查询尚未完成模块化。三个写方法共用任务、钱包和借记／退款事务；第四个通用方法包含十三种供应商的一次状态查询。完整迁移六方法可以关闭这一族，而完整生成、Worker 和 React 继续按整体目标推进。

本五稿仅在 EVID staging，普通 OpenSpec change 候选为 modularize-backend-generic-admin-tasks。本轮只准备规划和作者冻结收据，未安装到 target、未批准实施、未运行产品。当前 Task Observation 正在接线与验收；其进行中的 app/旧十代码不作为本批稳定基线。root 在当前批实际验收后建立本批真实 baseline，并按已有持续授权继续普通 OpenSpec 流程；不切换 spec-superflow，不新增人工 approval 或部署门槛。

## What Changes

- 完整登记 POST /api/tasks、PUT /api/tasks/{task_id}、PUT /api/tasks/{task_id}/progress、GET /api/tasks/{task_id}/external-status、GET /api/admin/tasks、GET /api/admin/tasks/{task_id}，六方法成功响应均为 200。
- 通用身份保留 active 会员规则及 superuser 绕过；管理员身份只要求真实 SQL is_superuser，不增加会员有效性要求。窄公共认证 facade 在认证命名空间内消费原 authenticated 上下文，返回可信身份之前成功关闭认证 Session。
- 任务创建使用 autoflush=False 的 ORM 暂存，任务／钱包／借记同一次 commit 后真实 refresh。保留三来源受控 guard、退款证据歧义、None 与空串以及原 JSON 错误路径；未知提交和提交后读回失败不自动重放。
- 管理员查询保留全账户、原九字段列表和不同的九字段详情、truthy 过滤、SQL ILIKE 通配、count-before-list、原 getattr 排序和宽分页规则。
- 独立 provider_status 包完整实现十三种单次 GET、配置 first 选择、显式 SECRET_KEY 解密、状态大小写、URL 提取顺序、超时、IPv4 和错误分层；不是恒返回 unknown，也不执行 runner.run、下载、OSS 或任务终态写入。
- 先冻结共享合同，再由 G/A/P 三组实现私有文件；公共 exports、app、登记哨兵及依赖声明保持单一 writer，随后独立 source review 和 root 实跑验收。

## Capabilities

### New Capabilities

- generic-admin-tasks：通用任务四方法、管理员任务两方法，以及完整十三供应商的一次外部状态观察。

### Modified Capabilities

- authentication 的公开接点仅新增窄 task identity facade 和必要 exports，原认证业务实现保留。
- 原接口行为和 Task Observation 已验收合同保持；旧登记测试只增加六方法／公开工厂的必要哨兵。

## Impact

未来候选路径详见 design 和 author-freeze.json：新增生产 26、新增测试/support 21，共 47 新代码路径；旧可变候选 13，形成候选 60 代码冻结键。旧可变只限 app、必要登记测试、authentication/__init__.py 和 backend/pyproject.toml；该路径图不是本轮写入 grant，实际 baseline 后逐项确认必要差分。代码与来源的两个 60 是不同集合。

固定来源位于旧仓库 Git object 23403806898550a7668a6ee7c0c457315655c39b。当前来源 55 保持，未来来源候选为 55+5=60；新增 tasks.py、admin.py、async_task_runner.py、crypto_service.py、public_url.py。crypto/OSS 最小读取缺口已由 dependency-closure01 关闭。完整 blob 哈希不等于全文语义审查；阅读范围和失败历史仍逐层保留。

四旧文档完整前缀 EOF 追加，加新架构、新 verification 和本批 tasks 标记共七路径；具体阶段完成才由 root 更新标记和事实。中文阶段提交并推送已授权的新 origin，验证层次不混写。

现有命名分析的计数是 248 旧声明、82 已登记、166 声明差集。当前九方法尚未正式 root accepted；仅当九方法实际验收后基数才可到 91，再由完整六方法到 97，全部数量从实际注册集合重算。本稿不扣减 166，不改当前 schedule/markers/source 集合，不把登记数称为业务完成率。整体 backend 全部 → React 全部 → 最后语言评估保持；生成六方法、Worker fencing/recovery/退款、OSS 运行、真实 PostgreSQL/供应商、浏览器和部署各有自己的后续证据层。指定 GPT-5.6 Sol/xhigh Grillme 继续 offline pending，不探测、不替代。
