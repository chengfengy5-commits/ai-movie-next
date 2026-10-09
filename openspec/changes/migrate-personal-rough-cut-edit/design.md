# Design

## Context

动机见 proposal.md。依据固定旧 HEAD `23403806898550a7668a6ee7c0c457315655c39b` 的 rough_cut.py 与 rough-cut.js；13 个源文件 SHA/字节及旧库实际 HEAD/status 已记录在 rough-cut-edit-baseline.json。既有 React 面板只读，父章节导航和便签/续作各自完成验收。目标目录目前没有真实后端应用；本批前端与 fixture 实现不能算后端模块化。

## Goals / Non-Goals

**Goals:**
- 保持 canonical 读取、筛选、导航语义，独立组织本地编排与写入生命周期。
- 使完整集合核对、响应确认、同步门禁和显式恢复都有独立可验证边界。

**Non-Goals:**
- 不改父工作台/章节浏览、原读取 parser、导航 helper、便签/续作、旧测试和样本，不引入依赖。
- 不运行真实后端/数据库/队列/Worker/R2，不提交推送部署，不接入播放导出或生成/收费。
- 指定 GPT-5.6 Sol/xhigh Grillme 为独立离线待办，Sol/Luna/root 的检查不能替代。

## Decisions

### 模块与文件边界

选择纯规则、生命周期 hook、无请求的编辑器三层；不把全套编辑状态塞进原只读面板，也不另造父级导航协议。

允许修改的旧产品路径仅五项：
- frontend/src/shared/api/services.ts
- frontend/src/shared/api/personalRoughCut.ts（整段原读取内容冻结，只允许 EOF 追加）
- frontend/src/features/chapters/PersonalRoughCutPanel.tsx
- tools/api-fixture/server.mjs
- frontend/src/styles.css

新增产品模块三项：
- frontend/src/features/chapters/personal-production/roughCutEdit.ts
- frontend/src/features/chapters/PersonalRoughCutEditor.tsx
- frontend/src/features/chapters/usePersonalRoughCutEditor.ts

新增测试五项：
- frontend/src/features/chapters/personal-production/roughCutEdit.test.ts
- frontend/src/features/chapters/PersonalRoughCutEdit.test.tsx
- frontend/src/shared/api/personal-rough-cut-edit.test.ts
- frontend/src/shared/api/personal-rough-cut-edit-http.test.ts
- frontend/src/app/PersonalRoughCutEdit.integration.test.tsx

因此代码/测试白名单为 13 路径。规划仅本 change 的 .openspec.yaml、proposal.md、design.md、tasks.md、specs/personal-rough-cut-edit/spec.md；verification.md 是事后证据。收尾仅追加 README.md 和 docs/architecture/module-api-compatibility.md 的第21切片段。旧 250 路径除五产品与两收尾文档外均冻结（243）；前20 change 的123文件、62重点保护文件及全部48旧测试冻结。基线和新白名单做实际 SHA、inventory 检查，不以 unborn Git 的空 diff 代替。

### 编排与取消

复制全部原顺序只修改 included 与顺序，移动按旧 JS 同 included 组邻居交换原数组位置；切换纳入不挪动原位置。编辑期间 canonical 列表仍可只读核对。Cancel 关闭编辑器并保留同实例草稿，重开继续；面板关闭/范围变更后丢弃，不建跨实例私有缓存。这里是明确的新 UI 策略，不声称与旧关闭确认完全一致。

### 严格请求与当前源资格

更新 parser 在原 DTO EOF 追加，顶层仅 expected_revision/frames、每项仅 asset_id/included，检查 Unicode 码点一至36、不 trim、布尔严格、唯一0..500、安全非负版本且+1可表示。合法空章 revision0 可首次显式保存，不依赖空资产加载。已保存无变更且无 pending/removed 为 no-op；pending/removed允许用户显式全量确认，这是旧 API 合法但旧 JS 的 !dirty 曾挡住的新入口。

非空章保存必须 assets ready 且原 asset request token/generation 与读票一致；先对全章 raw storyboard[0] 计重，再核对归属，不直接复用导航 helper 的 trim/nonblank 门槛。canonical 的完整 ID/位置和当前源一致；任何无效身份整章挡写，不能过滤掉已排除项。无视频或便签状态不挡写。

### 写入确认

API adapter 原路 PUT 一次 encode 的 chapter ID，复用当前 Bearer/现有 loopback 检查与15秒超时，不扩展其他 endpoint。严格解析返回并确认 chapter、R+1、saved、完整有序ID与included、pendingfalse/removed空；允许后端当前 source_frames 的 frame_index/text/preview/missing_reason变化，因为后端只存两字段，不加媒体CAS。成功后 canonical替换返回。若维护后的位置暂不能与本地旧章节对应，继续沿原导航guard提示重读章节；不倒判已可信的保存为unknown，不自动重基。

### 生命周期与门禁

hook 使用同步 ref gate、布局 scope 失效、永久实例墓碑、读取票/编辑会话代次和 mounted/请求代次。beforeSave先关闭gate，再abort/invalidate父导航/发PUT；旧回调必须绑定原读票、编辑代次和draft身份。旧 Panel 公开 read/close 回调也加相同合法票守卫。

pending及任意拒绝/unknown保留具体有序布尔意图，只读；Cancel/重开不能绕过。仅显式同章当前GET成功重置canonical、草稿和gate；失败GET仍保留意图。不能autoGET、retry、replay/rebase。当前401沿原onUnauthorized；无论abort是否被服务执行，旧成功/401都不修改新状态。身份/服务/章节变化第一次layout隐藏旧内容，ABA永久不恢复。便签门禁独立，不扩大为整个章节写锁。

### demo 与 fixture

demo在服务实例内用 user+chapter 私有Map，首次读取保持冻结样本，保存CAS并以当前源字段返回新canonical，实例重建清空。fixture维护 owner+chapter独立Map，旧GET模式、默认样本、鉴权和错误优先级保持。新增PUT只有完整集合和当前R通过才原子写；新测试模式使用 FIXTURE_ROUGH_CUT_SAVE_MODE/STATUS/DELAY_MS，scope为rough_cut_save，覆盖 revision-conflict/body-timeout/applied-invalid-structure/source-maintenance-success，旧 rough_cut GET模式不混用。只有 rough-cut PUT使业务方法模式13→14。日志仅 method/pathname/status，不记录凭据或正文。

### 验证方式

保留所有失败日志；直接调用实际 React JSX 公开回调（清晰mock边界），不用Fiber内部或脱离DOM旧元素冒充回调。每个旧回调单独act并立刻断言，使用至少两个非默认ready草稿/读取代次与Cancel重开会话。忽略abort的原GET/PUT成功和401需真正settle再await act；用真实Workspace切换两个账号，不以token丢失代替。真实loopback HTTP验证exactbody/CAS/隐私/拒绝与未知应用后重读，所有旧回归全量运行。最终冻结代码后root typecheck/test/build、source baseline、strict OpenSpec、文本和conservation、桌面移动键盘浏览器；截图与API方法日志分别记录。

## Risks / Trade-offs

- [本地fixture不是真实后端持久化] → 明确仅本地验收，真实后端模块化/联调保持独立未完成项；本批后优先形成后端边界。
- [响应丢失时服务端可能已应用] → 保留意图并锁写，显式GET成功后重新决策，禁止自动重放。
- [服务器维护source位置而章节未重读] → 信任合法PUT编排确认，导航依当前源guard，提示重读章节而非加入媒体CAS。
- [旧回调或迟到401串用] → 同步门禁/永久scope墓碑/读与编辑代次票，实际延迟Promise测试。
- [长面板继续膨胀] → 新逻辑收在三模块，只读面板仅集成控制与canonical更新。

## Migration Plan

只在隔离haoai-next实施；先冻结规划与历史，再分工实现并审查，执行本地全量/浏览器验收后追加事实文档。无上线切换；回退是禁用可选save能力并返回原只读入口。请求路由和旧环境保持现状，外审完成前不归档change。
