# Specification: backend-canvas-data

## Purpose

定义隔离后端对既有章节共享画布 GET/PUT 行为的兼容要求。

## ADDED Requirements

### Requirement: 画布是章节共享资源

模块 MUST 只提供 GET 和 PUT /api/chapters/{chapter_id}/canvas。请求必须使用真实身份与会员依赖，并在同一业务 Session 中复用既有 series-access policy。先查找章节；章节不存在时返回原有 404，再执行剧集访问检查。画布不按用户拆分。

#### Scenario: 两名有权成员读取同一画布

- **WHEN** 两名有权成员读取同一章节的画布
- **THEN** 两次读取返回同一章级画布记录
- **AND** 不创建按用户区分的画布行或唯一键

#### Scenario: 章节不存在

- **WHEN** 请求的章节不存在
- **THEN** 在 series-access 查询之前返回原有章节 404

### Requirement: 缺失画布返回字面空文档

当章节存在且访问通过，但尚无画布行时，GET MUST 返回 version 1、id 为 null、updated_by 和 updated_at 为 null 的文档；document_json MUST 等于版本 1、默认 viewport {x:0,y:0,zoom:1} 以及空 nodes、edges、notes 的完整对象。该 GET MUST NOT 插入画布行或提交业务事务。

#### Scenario: 首次读取

- **WHEN** 有权成员读取尚无画布记录的章节
- **THEN** 返回完整默认画布
- **AND** 数据库仍无画布行且业务 commit 次数为零

### Requirement: 已存 JSON 保留原解析边界

读取字符串形式的 document_json 时，模块 MUST 尝试 JSON 解析；只对 JSONDecodeError 或 TypeError 回退为对象 {}。有效 JSON 的 null、数组或标量 MUST NOT 被额外规范化为空对象，仍由响应 DTO 的字典类型验证处理。

#### Scenario: 格式错误与有效非对象 JSON

- **WHEN** 存储值是无法解析的 JSON 字符串
- **THEN** 响应沿用旧行为返回空对象
- **WHEN** 存储值可解析为 null、数组或标量
- **THEN** 不把该值转换成默认空画布或空对象

### Requirement: 写入 DTO 与序列化保持兼容

PUT 请求 MUST 要求 document_json 为对象；version 为可省略或显式 null 的普通可选整数，并保留原常规 Pydantic 转换和额外字段忽略行为。模块 MUST NOT 增加严格整数、版本正数限制、节点白名单、图结构、renderer 或其他新校验。

序列化 MUST 使用 json.dumps(document_json, ensure_ascii=False) 的默认分隔符；不得通过复制或规范化 payload 改变其内容。大小按序列化字符串的 Python 字符数计算，只有长度大于 1,000,000 时返回原有 400；这不是 UTF-8 字节上限。

#### Scenario: 可省略版本与普通对象

- **WHEN** version 缺省、为 null 或可按既有模型转换，且 document_json 为对象
- **THEN** 按原 DTO 接受或拒绝输入
- **AND** 不拒绝被忽略的额外字段

#### Scenario: 字符数大小边界

- **WHEN** 序列化结果长度恰为 1,000,000 个 Python 字符
- **THEN** 不因该上限返回 400
- **WHEN** 序列化结果长度超过 1,000,000 个 Python 字符
- **THEN** 返回原有画布过大 400

### Requirement: 锁与错误顺序保持不变

PUT MUST 在大小及版本检查之前，在同一业务 Session 中尝试续期当前成员已有的本人章节锁。本人锁即使已过期也按既有逻辑续期；没有锁或锁属于其他成员时，模块 MUST NOT 因此新建锁或拒绝写入。falsey 锁时长仍使用既有 15 分钟回退，负数配置保持原值。

顺序 MUST 保持为章节查找、series access、本人锁刷新、序列化和大小检查、画布读取、可选版本比较与写入。拒绝请求在提交前失败时 MUST 回滚锁刷新等同事务改动。

#### Scenario: 本人过期锁

- **WHEN** 当前成员持有本人锁且其过期时间已过
- **THEN** 写入流程仍按旧配置续期该锁

#### Scenario: 无锁或他人锁

- **WHEN** 当前成员无锁或锁由他人持有
- **THEN** 不新建锁、不因锁状态增加拒绝

#### Scenario: 大小检查先于版本读取

- **WHEN** payload 超过大小限制且携带过期版本
- **THEN** 大小错误按既有顺序先返回

### Requirement: 创建与更新保留版本行为

首次保存 MUST 忽略请求携带的 version，创建 version 1。已有画布时，只有请求 version 非 null 才与已读快照版本比较；不匹配返回原有 409。匹配或未提供时，更新版本为 (current_version or 1) + 1。成功写入一次业务 commit。相同内容的成功 PUT 仍递增版本。

实现 MUST 保留基于已读快照的比较与按记录主键更新，不增加数据库 WHERE version 条件或其他数据库 CAS，以免悄悄改变现有并发覆盖行为。

#### Scenario: 创建携带任意版本

- **WHEN** 章节还没有画布行且请求带有 version
- **THEN** 忽略该版本并以 version 1 创建

#### Scenario: 已有画布的版本冲突

- **WHEN** 请求提供的非 null version 与已读画布版本不同
- **THEN** 返回原有 409，不写入画布

#### Scenario: 相同内容再次保存

- **WHEN** 有效 PUT 的 document_json 与当前内容相同
- **THEN** 仍更新版本并提交一次

### Requirement: 成功响应使用刷新后的元数据

成功 PUT MUST 在一次业务 commit 后，使用同一 UoW 对已写行执行真实刷新/重读。响应中的 id、chapter_id、version、updated_by、updated_at MUST 来自刷新后的持久行；响应的 document_json MUST 回显本次请求对象。后续 GET MUST 读取实际存储内容。

#### Scenario: 提交后持久层维护元数据

- **WHEN** 存储层在提交期间维护返回元数据
- **THEN** PUT 响应反映刷新后的记录元数据，同时回显该请求的 document_json
- **AND** 随后的 GET 返回持久层当前内容

### Requirement: 未知提交结果不自动重放

提交之前发生故障时，UoW MUST 回滚本次事务。若 commit 已成功但确认或刷新失败，调用结果 MUST 作为未知处理；模块 MUST NOT 自动重发写入，调用方需显式 GET 核实持久状态。不得声称回滚能撤销已经成功的数据库 commit。

#### Scenario: 提交前失败

- **WHEN** 写入阶段在 commit 前失败
- **THEN** 事务回滚且画布保持原状态

#### Scenario: commit 后确认失败

- **WHEN** 数据库已提交而确认/刷新发生失败
- **THEN** 不自动重试写入
- **AND** 显式 GET 才用于核实当前持久状态

### Requirement: 完整画布持久化约束

完整 owner 表 MUST 保留画布 id、series_id、chapter_id、version、Text 型 document_json、created_by、updated_by、created_at、updated_at，章节唯一约束以及对 series、chapter、users 的既有外键。新投影不得覆盖或扩展既有 series_data 删除流程中的薄画布投影。

#### Scenario: 历史行守恒

- **WHEN** 画布被读写
- **THEN** 只有本次画布及按原行为刷新的本人锁字段允许改变
- **AND** 用户私有记录、源数据、任务、账单和队列历史行保持不变
