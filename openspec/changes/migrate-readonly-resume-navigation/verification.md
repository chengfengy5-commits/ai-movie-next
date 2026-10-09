# Verification

## 范围与来源

本记录对应 migrate-readonly-resume-navigation。实现复用个人制作记录现有的可信续作位置投影，仅在已加载章节内做显式本地导航；未新增业务接口、写入、媒体元素或主动媒体请求，也没有扩展旧来源复制；滚动可能使既有 lazy 原图自然加载。固定旧来源提交为 23403806898550a7668a6ee7c0c457315655c39b；四个来源文件的 SHA-256 与设计记录匹配，前七 change 的 42 份历史文件 hash 检查结果为 changed: []。来源 verifier 通过原批准 exact-five 清单，未发现敏感模式。这些是固定快照和静态来源证据，不是实时后端验证。

根侧证据文件位于 /Users/yanghaibo/.codex/visualizations/2026/10/03/01a10263-a301-7f52-bc58-57ce7d17bc80/：

- haoai-next-resume-navigation-root-checks.json：根侧命令、最终管线、请求计数及清理结果。
- haoai-next-resume-navigation-browser-observations.json：真实浏览器观察记录。
- haoai-next-resume-navigation-final-audit.json 与 haoai-next-resume-navigation-api-requests.jsonl：接口日志审计、来源与历史 hash 结果。
- 截图：haoai-next-resume-navigation-demo-desktop.jpg、haoai-next-resume-navigation-demo-mobile.jpg、haoai-next-resume-navigation-api-first-account.jpg、haoai-next-resume-navigation-api-second-mobile.jpg、haoai-next-resume-navigation-api-error.jpg、haoai-next-resume-navigation-demo-final.jpg。

## 自动验证与静态检查

root 独立执行的最终 frontend pipeline 全部 exit 0：

- npm --prefix frontend run typecheck。
- npm --prefix frontend test -- --reporter=dot：25 个测试文件，393/393 用例通过。
- npm --prefix frontend run build：Vite 转换 59 个模块；CSS 38.25 kB（gzip 8.34 kB），JavaScript 337.37 kB（gzip 99.59 kB），构建约 1.05 秒。
- node --test scripts/source-baseline-contract.test.mjs：3/3 通过。
- npm --prefix frontend run verify:source：验证固定基线 23403806898550a7668a6ee7c0c457315655c39b 上批准的 5 个来源条目，敏感模式命中 0。
- openspec validate migrate-readonly-resume-navigation --strict：root 实际执行，valid，exit 0。

自动测试覆盖当前 verified 目标、needs_reconfirmation 可定位、没有自动跳转、无效/缺失/mismatch/pending/error 时禁止定位、重读立即失效，以及 scope 首帧、A→B→A 和真实 settle+flush 的旧 success/401/digest/callback。ChapterBrowser 自动用例验证当前目标、程序化 focus/scroll 调用与反馈、保持记录面板打开、重复定位不增加业务请求，以及切章、重读和会话变化时失效；focus/scroll spy 不等同于实体键盘操作。键盘 Enter 操作由下方 root 真实浏览器实测。代表性用例不声称覆盖每种状态的全组合。

root 在文档落盘后执行最终文本扫描：124 个文本文件；UTF-8、EOF newline、尾空格、冲突标记和控制字符检查均为 issues: []。证据保存在 haoai-next-resume-navigation-closeout-scan.json。旧 HEAD 仍为 23403806898550a7668a6ee7c0c457315655c39b；前七 change 的 42 个历史文件 hash 未变化。该次状态检查仅发现原有未跟踪 .spec-superflow.yaml；其内容未读取。未提交、推送或部署。

## 根侧真实浏览器验收

root 使用默认 demo 及 loopback API fixture 做独立浏览器验收。Demo 在桌面 1280×900、390×844 和键盘操作中，按 Enter 可定位并聚焦镜头 1；目标滚动进入视口，显示位置/状态反馈，个人记录保持打开。读取与重读不会自动跳转。关闭、记录重读、切章、互斥面板切换和返回路径均检查了定位/反馈清理。

API 模式使用两个合成账号读取同一章节，各自定位到 fixture 中不同的镜头位置（账号一为 1，账号二为 2）；点击前后的业务请求计数相同。第二账号的 390px 页面没有横向溢出。500 错误后，显式重试成功仍要用户再点击定位。无效结构、无续作位置和 unreadable 记录下不显示定位入口；pending 时关闭记录面板并等待 5 秒，之后仍未重新显示面板、定位入口或旧高亮。

API 审计为 53 条记录：22 条业务请求、25 条 OPTIONS、6 条已有本地 PNG；实际访问 5 种业务路由模式，unexpected 为空。现有白名单支持 12 条业务路由，但此次浏览器未访问全部 12 条。定位没有新增媒体元素或主动 fetch；6 次是既有 loopback 安全 PNG 读取，滚动可能触发现有 lazy 图片自然加载。

root 已停止本次 4175 fixture 和 5174 API Vite 服务、关闭 API 页签并恢复 viewport；根侧保留的 5173 demo 会话与页面不属于新增 API 服务。

## 独立只读复审

GPT-6.1 Sol / xhigh 对实现与测试进行了独立只读复审，没有发现未解决的实证 P1/P2。复审确认首个 layout commit 中服务/章节 scope 改变时旧入口隐藏、A→B→A 不复活，以及回放旧 target callback（包括把 isCurrent() 强制为 true）也无法绕过父级当前作用域守卫。hidden/inert/disconnected 三个守卫分支仅有静态代码核对，不声称各自有单独自动反例。该复审没有运行测试或浏览器，也不代替 Grillme。

## 未运行与待办

本 change 未启动或连接真实 FastAPI、PostgreSQL、Worker、R2、真实媒体或生产服务；没有发布。Grillme 探测离线（curl exit 7，HTTP 000），固定 GPT-5.6 Sol / xhigh 独立审查仍未完成，对应 task 5.1 保持未勾。文档 task 4.1 已完成，root 的最终文本扫描证据见上文。无提交、推送或部署。
