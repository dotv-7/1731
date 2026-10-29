# dotv Hermes：全局说明文档（持续维护）

> 本文档由 Devin 维护，是这个分支所有自定义功能的总入口。Hermes 每升级一个版本、或者权限设计每改一次，都要同步更新本文档。
> 基线版本：Hermes **v0.22.2**（快照 `74e7a25c`）。工具清单和上下文来源都是从这份源码里整理出来的。

---

## 0. 定死的要求（不可违背）

| # | 要求 | 来源 |
|---|---|---|
| R1 | **可插拔**：新 UI、上下文隐私层、权限闸门全部做成外挂，通过官方扩展点（profile、插件钩子、memory provider、`/v1` API）接入，不大改 Hermes 内核。Hermes 升级后应能直接装回，最多做小幅适配。 | 用户 |
| R2 | **主体与官方最新正式版完全对齐**：fork 只是一层薄改动，每个 feature 独立 commit；上游实现了同样的功能就删掉我们的版本。 | 用户 |
| R3 | **隐私对象是“上下文”而不只是 memory**：凡是能进入 Hermes 上下文、和 Owner 有关的信息（MEMORY.md、USER.md、自传等 workspace 文件、历史会话、skills、context 文件……）都在管控范围内。 | 用户 |
| R4 | **默认私密，白名单公开**：任何内容不标“公开”就是私密；标签写错、漏标、未知标签一律按私密处理。 | 用户 |
| R5 | **必须有上下文看板**：Owner 能看到当前所有会进入上下文的内容，并逐条决定公开还是私密。 | 用户 |
| R6 | **硬隔离优先，提示词只是兜底**：访客拿到的上下文里物理上不存在私密内容；系统提示里写明“这是访客会话”只作为第二道保险。 | 用户 |
| R7 | **访客不能操作本地系统**：可以搜索、可以用基础功能，但不能读写文件、执行终端或代码、操作浏览器、查看历史会话。 | 用户 |
| R8 | **实现 527 的操作权限分级**：第一版只做 Owner 和 Guest，User 和 Admin 先留接口。 | 用户 |
| R9 | **独立 UI**：与官方 dashboard 分开，终端风格，使用体验要好。可验证指标包括：逐字流式输出、键盘优先、上箭头历史、斜杠命令补全、Ctrl+C 中断、代码和表格渲染、断线自动重连、刷新后会话保留、手机端可用。 | 用户 |
| R10 | **不丢会话**：生产环境 `sessions.auto_prune: false`，超过 90 天的会话一律保留。 | 用户 |
| R11 | **官方 dashboard 只给 Owner 用**：它内嵌了真实 PTY 终端，永远不对访客开放。 | 设计约束 |

## 1. 最终成品清单

| # | 成品 | 内容 | 状态 |
|---|---|---|---|
| D1 | **上下文隐私功能 + 看板** | (a) 上下文盘点器：扫描所有上下文来源（见 §4），列出每一条内容；(b) 看板：仅 Owner 可用，逐条标记公开或私密，默认私密；(c) 公开投影同步器：把公开条目单向同步到 `public` profile；(d) 帮助 Owner 分类的 skill（只给建议，Owner 确认后才生效） | 设计中 |
| D2 | **527 操作权限分级** | Owner/Guest 分级（User/Admin 留接口）；按工具名单拦截调用（`pre_tool_call` 闸门插件）；命令权限；在会话上下文里注入当前身份 | 设计中 |
| D3 | **独立 UI** | 终端风格网页聊天（风格待选，样稿 3 选 1），后端小服务持有 API key，访客和 Owner 走不同 profile，满足 R9 的指标 | 样稿已出 |
| D4 | **全局说明文档** | 本文件：要求、成品、工具清单、上下文来源、角色权限矩阵、上游改动清单 | 本文件 |
| D5 | 附带：Gemini Google Search 补丁、GHCR 镜像 | `ghcr.io/dotv-io/hermes-agent:v0.22.2-dotv.N`，详见 `RESEARCH_527.md` §7 | 待做 |

## 2. 527 管什么，我们管什么

527 原文（`NousResearch/hermes-agent#527`）加上维护者 `teknium1` 的补充评论对比结论如下：**527 本质上是“操作权限”的多级管理**，上下文权限只顺带提了几句。

| 维度 | 527 | 我们（D1） |
|---|---|---|
| 要防的问题 | 能力被滥用：员工执行 `rm -rf`、乱改模型、乱跑定时任务 | 隐私泄露：访客从对话里套出 Owner 的私事 |
| 管控对象 | **工具和命令**：权限表二十多行，几乎全是工具和斜杠命令 | **上下文内容**：每一条可能进入上下文的信息 |
| memory | 只有一行：“Memory read/write：Owner/Admin 读写，User 只读，Guest 无”。按人整体开关，不区分条目 | 按**条目**区分公开/私密，访客能看到公开条目 |
| 上下文 | 只有三处：① 在会话上下文里注入当前说话人的等级；② 维护者评论里提到 workspace 清单只给有读权限的等级注入 system prompt；③ 指出共享会话会让所有人看到同一份历史和 memory | 全部上下文来源都纳入看板（§4） |
| 粒度 | 按人分级（Owner/Admin/User/Guest） | 按内容分级（公开/私密），再和人的等级组合 |
| 实现位置 | 改 gateway 和工具分发（内核） | profile 隔离加插件（外挂，R1） |

**两者如何组合：** 527 决定某个人**能做什么**（工具和命令），D1 决定某个人**能知道什么**（上下文）。最终每个角色的权限 = 工具名单 × 上下文可见范围，见 §5。

## 3. Hermes 全部工具（v0.22.2）

来源：`toolsets.py` 的 `TOOLSETS`。Owner 默认全部可用，部分工具需要配置 key 或对应平台才会出现。

| 工具组 | 工具 | 风险说明 |
|---|---|---|
| web / search | `web_search`、`web_extract` | 自带 SSRF 防护（`tools/url_safety.py`），会拦截内网地址 |
| x_search | `x_search` | 外部搜索 |
| vision / video | `vision_analyze`、`video_analyze` | 能读本地路径的图片和视频，访客只允许 URL 或访客自己上传的文件 |
| image_gen / video_gen | `image_generate`、`video_generate`、`xai_video_edit`、`xai_video_extend` | 消耗 API 额度 |
| tts | `text_to_speech` | 消耗 API 额度 |
| clarify | `clarify` | 无风险 |
| file | `read_file`、`write_file`、`patch`、`search_files` | 直接读写文件系统 |
| terminal | `terminal`、`process_manage` | 完全控制主机 |
| code_execution | `execute_code` | 沙箱能读到本地文件 |
| browser | `browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、`browser_scroll`、`browser_back`、`browser_press`、`browser_get_images`、`browser_vision`、`browser_console`、`browser_cdp`、`browser_dialog`、`browser_vault`、`browser_exec` | 能用到已登录的网站和密码库，也能打开 `file://` |
| computer_use | `computer_use` | 控制桌面 |
| memory | `memory` | 读写 MEMORY.md 和 USER.md |
| session_search | `session_search` | 搜索 state.db 里**所有历史会话** |
| skills | `skills_list`、`skill_view`、`skill_manage` | 能读写 skill 文件 |
| todo | `todo_list` | 会话内任务清单 |
| cronjob | `cronjob_manage` | 定时执行任意任务 |
| delegation | `delegate_task` | 子代理继承工具 |
| kanban | `kanban_show`、`kanban_list`、`kanban_complete`、`kanban_block`、`kanban_request_review`、`kanban_request_changes`、`kanban_heartbeat`、`kanban_comment`、`kanban_create`、`kanban_link`、`kanban_unblock`、`kanban_attach`、`kanban_attach_url`、`kanban_attachments` | 读写看板数据 |
| connections / catalog | `manage_connections`、`manage_catalog` | 管理账号连接和插件目录 |
| 平台 | `discord`、`discord_admin`、`yb_*`（元宝 5 个）、`feishu_doc_read`、`feishu_drive_*`（4 个）、`send_message` | 能代表 Owner 对外发消息 |
| 桌面版专用 | `desktop_ui`（11 个）、`desktop_project`、`setup_beat` | 只在桌面版里出现 |
| MCP / 插件 | 动态注册 | 默认全部拒绝 |

> 维护：每次升级后用脚本重新导出 `TOOLSETS`，和本表做 diff。

## 4. 所有可能进入上下文的来源（v0.22.2）

Hermes 的上下文由两部分组成：A 是**自动注入**的 system prompt，在会话开始时固定，见 `agent/system_prompt.py::build_system_prompt_parts`；B 是**运行时进入**的内容，包括对话本身、工具结果和每轮注入。

### A. 自动注入（会话开始时固定）

| # | 来源 | 位置 / 代码 | 含个人信息？ | 访客默认 |
|---|---|---|---|---|
| A1 | `SOUL.md`（人设） | `$HERMES_HOME/SOUL.md`，`load_soul_md` | 可能有 | public profile 用自己的人设 |
| A2 | 项目 context 文件 | cwd 下的 `.hermes.md`/`HERMES.md`、`AGENTS.md` 链、`CLAUDE.md`、`.cursorrules`、`.cursor/rules/*.mdc`，见 `build_context_files_prompt` | 可能有 | 不注入（public 的 cwd 是空目录） |
| A3 | workspace 快照 | `agent/coding_context.py`（当前目录的 git 状态） | 可能有 | 不注入（没有工具就不生成） |
| A4 | skills 索引和自动加载的 skill | `build_skills_system_prompt`、`skills.auto_load` | 可能有（自写 skill） | 不注入（没有 skills 工具就不生成） |
| A5 | 内置 memory：`MEMORY.md` | `$HERMES_HOME/memories/MEMORY.md` | **有** | 只注入公开投影 |
| A6 | 用户画像：`USER.md` | `$HERMES_HOME/memories/USER.md` | **有** | 只注入公开投影 |
| A7 | 外部 memory provider 的提示块 | `MemoryProvider.system_prompt_block` | **有** | public 不配置 provider |
| A8 | 插件注入的提示段 | `_plugin_section_blocks(..., "after_memory")` | 视插件而定 | 只装白名单插件 |
| A9 | 环境提示、profile 名、时间 | `build_environment_hints`、`_active_profile_line` | 主机路径 | 无害化 |
| A10 | 会话上下文块（平台、频道、说话人） | `gateway/session.py::build_session_context_prompt` | 平台身份 | 注入“访客”身份 |

### B. 运行时进入

| # | 来源 | 说明 | 访客默认 |
|---|---|---|---|
| B1 | 对话历史 | 当前会话的消息；恢复会话时会加载旧消息 | 只有访客自己的会话 |
| B2 | 工具结果 | `read_file`、`search_files`、`terminal`、`browser` 等工具的返回内容。**自传这类 workspace 文件主要是通过这条路进入上下文的** | 相关工具全部关闭 |
| B3 | `session_search` | 能搜出所有历史会话 | 关闭 |
| B4 | `memory` 工具读取 | 能读出完整的 MEMORY.md 和 USER.md | 关闭 |
| B5 | `skill_view` | 能读出 skill 的全文 | 关闭 |
| B6 | 外部 provider 每轮预取 | `prefetch_all`，附加在用户消息上 | public 不配置 |
| B7 | 插件每轮注入 | `pre_llm_call` 返回的上下文（附加在用户消息上），见 `agent/turn_context.py` | 只装白名单插件 |
| B8 | 压缩摘要 | 长会话压缩后生成的摘要 | 只来自访客自己的会话 |
| B9 | 上传和附件 | 用户上传的文件、图片 | 只有访客自己上传的 |
| B10 | 子代理 | `delegate_task` 会把上下文传给子代理 | 关闭 |

**关于“自传”：** 自传放在 workspace 目录里，不在 A2 的固定文件名里，所以不会自动注入。只要 Hermes 有 `read_file`/`search_files`/`terminal`，它随时能读到（B2），聊过的内容也会留在历史会话里（B1/B3）。所以看板不能只扫 memory，还要扫 workspace 文件、历史会话和 skills。

## 5. 角色权限矩阵（草案，第一版只实现 Owner 和 Guest）

| 角色 | 工具 | 上下文 | 状态 |
|---|---|---|---|
| **Owner** | 全部（§3） | 全部（§4） | v1 |
| **Guest** | `web_search`、`web_extract`、`clarify`；可选 `vision_analyze`（仅 URL 或上传文件）、`image_generate`、`text_to_speech` | A1 公开人设、A5/A6 公开投影、A9 无害化、A10、B1/B8/B9 访客自己的；其余全部不给 | v1，可选项待用户定 |
| **User** | 527 默认：web、读文件、vision、image_gen、memory 只读 | 公开投影，外加 Owner 授权给 User 的条目（预留 `[visible: user]`） | 留接口 |
| **Admin** | 527 默认：除 `/update` 外全部 | 全部，或按 Owner 授权 | 留接口 |

两道闸：① **profile 隔离**，访客的 agent 物理上拿不到私密上下文；② **`pre_tool_call` 闸门插件**，不在名单里的工具一律拒绝。

## 6. 架构（可插拔）

```
浏览器 ──► dotv-chat（独立容器：终端风格 UI + 小后端，持有 API key）
              │  Owner（登录后）          │  Guest
              ▼                           ▼
        Hermes /v1 (default)        Hermes /p/public/v1 (public profile)
        全部上下文 + 全部工具        公开投影 + 白名单工具 + 闸门插件
              ▲
   上下文看板（Owner 专用）── 盘点 §4 的所有来源 ── 标记公开/私密 ── 同步器 ──► public profile
```

| 组件 | 依赖的官方接口 | 升级风险 |
|---|---|---|
| dotv-chat UI | `/v1/chat/completions` 流式接口、`/p/<profile>/` | 低 |
| 公开投影同步器 | MEMORY.md/USER.md 格式、profile 目录结构、`on_memory_write` | 低–中 |
| 上下文看板 | 只读扫描 `$HERMES_HOME`、state.db（只读）、skills 目录 | 中（state.db schema 可能变） |
| 闸门插件 | `pre_tool_call` 钩子 | 低 |
| 527 按说话人分级（群聊场景） | `pre_gateway_dispatch` / `pre_tool_call` 钩子；同一个群里 Owner 和 Guest 混聊时，上下文怎么分开还没解决 | **待评估** |

## 7. 待定事项

1. UI 风格：样稿 3 选 1（Hermes 同款 / 复古绿屏 / 极简浅色）。
2. Guest 可选工具：看图、生成图片、语音朗读要不要开。
3. Owner 认证：口令、账号登录，还是两者都要。
4. 看板中“公开”标记存在哪里：写进原文件（如 `[visible: guest]`），还是另存一份清单（不改原文件）。
5. 527 群聊场景（同一个群里既有 Owner 也有 Guest）是否在 v1 范围内。

## 8. 维护记录

| 日期 | 变更 |
|---|---|
| 2026-10-29 | 初版：要求、成品清单、527 对比、工具清单、上下文来源、权限矩阵草案、架构 |
