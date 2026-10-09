# 冻结来源基线

## 来源与工作范围

- 旧库：`/Users/yanghaibo/data/projects/ai/haoai-修改版/haoai.zhuwh.com`。
- 冻结提交：`23403806898550a7668a6ee7c0c457315655c39b`，本轮已通过 `git rev-parse HEAD` 核实。
- 旧库 `git --no-optional-locks status --short --branch` 显示 `main...origin/main`；跟踪文件无改动，仅有未跟踪 `openspec/changes/harden-task-billing-and-redeployment/.spec-superflow.yaml`。该文件不读、不改、不复制。
- 新库：`/Users/yanghaibo/data/projects/ai/haoai-next`，与旧库独立；本切片只在新库写入规划、前端与隔离验证材料，不生成 commit、不推送、不部署。
- 旧来源一律读取固定 SHA 的 `git show <SHA>:<path>`，仓库命令明确指定旧库 workdir；不从旧工作树复制，不启动或导入旧后端，不执行旧测试/迁移，不访问旧数据库、模型、R2 或真实队列。

## 首切片最小复制白名单

以下四个完整文件加一个精确摘录仅可作为非运行参考基线复制，共五个参考产物。实现者须保留路径映射、冻结提交、Git blob、SHA-256 和字节数到 manifest，并重新核对来源和目标 bytes。它们不能被 Vite、Python 或启动脚本执行/导入。

| 来源路径/复制范围 | 原文件 Git blob | 目标内容 SHA-256 | 目标字节 |
| --- | --- | --- | --- |
| `backend/app/schemas/auth.py` | `c23e10dfa3d4bfd60deb2b17bf014c6addb3f5e2` | `faa48c5f3754d8a491d4310dc28133fa8e586f164af9356f94d1a419a87557f7` | 2419 |
| `backend/app/schemas/series.py` | `9527d41091cecba9065c04a45f0df0c8f2c35309` | `78b1aee6e31cc195fb9e9af7401738ed1c2b3a73a9563f196855dd9912e3bfb9` | 13748 |
| `js/auth.js` | `088edb72c579f1f3cf0fe298070e2d79c2e46931` | `1fcc256e1d6d985ca8d278f639ffe9bf9a243d4c3972d37f6f5f4a82f6b2771a` | 5006 |
| `backend/app/routes/auth.py`，完整参考 | `f58aad8ec20eaeae6df6205624821fa4d3d7990b` | `6cae3297fb5806c8668cbd245be974ff42ccf42999265440a6d6e27a7e6d1a78` | 18221 |
| `backend/app/routes/series.py`，第 103–174 行 | `6cd7f088ed3f6eb3250844cc0334597c7ffcc6ee` | `b1eb133ed6005be1f925f345bd67dc410d9871cfc1bbcace2bf3265ad846aa6a` | 3226 |

series 摘录保留上述首尾行及原始换行；manifest 另记完整来源文件 SHA-256 `db9721e34a3c5f22f81ce7cf4bc5434d9f3661d732ab1e8a79ba2e74852243cf` 与行号范围。其目标 hash 只对应允许摘录，不能拿它与整文件 bytes 比较。

本轮只读检查了这五份冻结参考内容的私钥头、常见供应商 key 前缀，以及敏感字段赋值字符串三类模式，命中数均为 0。哈希证明来源一致；模式检查仅覆盖上述规则，不能证明没有所有形式的秘密。复制实现和最终检查仍须记录实际执行，不得把这一静态检查当成全面凭据审计。

白名单外的路由、模型、服务、大型前端脚本及旧 OpenSpec 规格先只作静态阅读与精确行号引用，见 `docs/architecture/module-api-compatibility.md`。本轮没有授权整仓复制或把旧后端变成可运行新服务。若后续切片需要扩展复制范围，应在其 change 明确文件白名单与敏感内容检查。

## 复制规则

1. 来源路径须是相对仓库根的明确普通文件，无绝对路径、`..`、目录、通配符或符号链接；仅从冻结提交读取。
2. 复制前读取固定 blob 并检查字节；命中敏感内容须停止该文件复制并保留路径/规则说明，不打印实际值。
3. 目标只能是新库非运行参考目录，manifest 明确目标路径和 SHA；相同文件可复核，目标不同内容不得静默覆盖。
4. 不使用整仓 `cp/rsync/git archive`，不把“tracked 干净”解释为“没有秘密”。
5. 复制后核对数量、路径、来源/目标 hash 和文本内容检查；未跟踪来源不进入任何 manifest。

## 明确排除

- 所有 `.env*`、真实或示例运行配置、`js/config.js`、`js/config.runtime.js`、后端配置/数据库连接文件、密钥、token、日志、浏览器会话和用户数据。
- 数据库文件/备份/导出、上传与下载目录、媒体产物、压缩包、模型与供应商凭据、历史探针输出。
- `.git`、`node_modules`、虚拟环境、缓存、编译产物、IDE/机器本地设置及未跟踪文件。
- `deploy/`、systemd/nginx、启动脚本、运维脚本、迁移/seed/import/reset/recovery 脚本、旧 Worker 运行入口。
- 未跟踪的 `.spec-superflow.yaml`；新项目不启用 spec-superflow。

## 验证边界

本基线的 SHA、状态、行号和五份白名单参考内容 hash 来自本轮实际只读命令。来源复制、前端构建、HTTP 契约/UI 自动与真实浏览器应由实施验证记录分别证明；不存在 tracked diff 时，仅运行 `git diff --check` 不能覆盖全部新增文件，须直接检查实际新文本文件的空白/合并标记/敏感内容及 manifest。

HTTP fixture 能验证冻结接口和前端 fetch 路径，不能证明真实 FastAPI/PostgreSQL 会话、数据库权限、Worker 或 R2 已验收。Grillme 固定 GPT-5.6 Sol / xhigh；当前本机连接不可用，状态“未完成，离线”，不启动或反复配对、不改用其他模型、不冒充通过。旧库和真实生产服务保持未触碰。
