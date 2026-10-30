# WorkPilot 任务状态

当前：**T05 已接受，负责人授权本地提交，随后只实施 T06。**
提交前分支 `main`，HEAD 为 `dd804e6`，比本地跟踪引用 `origin/main` 超前 2 个提交；未推送或合并。
本轮先完成 T05 本地提交，再进入 T06。唯一规格：[V0_1_SPEC.md](V0_1_SPEC.md)。

规格原样保留：36042 字节，SHA-256
`cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb`。

## 规格疑问

- **负责人已裁决（2026-10-30）**：tracing 强制设为 `1`，T04 引入 SDK 时固定已验证版本；
  保留 `projects.allowed_files_json` 和 `projects.active`。普通移除删 chunks、停用来源，保留记忆/名称；
  确认清除后删除登记/来源/记忆，审计保留并解除关联。仍为七张表；未索引来源不造哈希/时间。
  doctor 不建库/迁移/修复，未初始化返回 6，FTS5 缺失与锁占用仅提示。
- **T02/T03 实现已接受**：长段续块保留真实连续行号，单行超过 2000 字符拒绝；失效删除 chunks，
  事务失败保留旧索引。status 取来源最大 `indexed_at`，每来源展示首块最多 400 字符；缺失/撤权/stat
  变化隐藏旧正文，仅时间过期保留并告警。mtime/size 无法发现同尺寸、同秒级 mtime 的改写，需 sync 校验哈希。
- **T04 实现已接受**：dry-run 预览“可返回的候选范围”，冻结全部可用来源概要、
  原问题 Top 8 摘录及人工记忆；工具不能扩大范围，实际可能只用部分。候选材料过大拒绝（4），
  每次模型请求另计序列化上下文预算；引用只允许已实际返回的切片及摘录可见行，避免引用被截断的后续行。
- **T04 实现已接受**：事实句判定采用保守的逐句引用覆盖检查，未覆盖则整答失败关闭；
  混合有效/无效引用按规格警示。校验只证明出处，不证明语义；无日期资料统一标注为上次同步读取到。
- **T04 实现已接受**：SDK 单 Agent/FunctionTool 用于单步模型请求，本地回路执行工具、
  权限与预算；SDK 回调不能绕过。引入 SDK 时同步关闭 tracing，审计持久化与完整安全收口仍属 T05。
- **T05 实现已接受**：问题哈希/字符数及计数使用 `summary_redacted` 的固定 JSON 字段，
  上限 200 字符，不新增列或表。现有状态枚举无 running，开始时记 `error/INTERRUPTED`、空完成时间，
  正常结束再更新；硬中断保留为“未完成”。`audit` 自身留记录但从当次输出排除。
- **T05 实现已接受**：沿用 doctor 只检查的裁决，不写审计；`project list` 未列入 action
  枚举，暂不记账。帮助/参数解析/配置失败不落库；注册校验失败仅向已有、安全且与来源分离的库记账，
  不为审计提前建库。已完成本地操作与审计收尾不是一个事务，后者失败需检查状态，不自动重做或回滚。

## 交接记录

历史完整六项交接保留在提交快照：
[T00 · 1369946](https://github.com/trainee398/Personal-Work-Agent/blob/1369946422bf3830f29d5874c73c01636bd54718/docs/STATUS.md)；
[T01 · 9cee427](https://github.com/trainee398/Personal-Work-Agent/blob/9cee427/docs/STATUS.md)；
[T02 与既有裁决 · 1554524](https://github.com/trainee398/Personal-Work-Agent/blob/1554524/docs/STATUS.md)。
T01 实现为 `f52c759`；T02 实现为 `e897e61`。T03 已按授权提交 `35f052e`，完整交接可本地查看
`git show 35f052e:docs/STATUS.md`；提交前 **296 passed in 2.37s**，ruff、格式、diff 检查通过。
T04 已按授权提交 `dd804e6`，完整交接见 `git show dd804e6:docs/STATUS.md`；提交前
**413 passed in 4.59s**，ruff、格式、diff 检查通过。以上本地提交未推送。

### 2026-10-30 · T05 本地审计与安全收口（负责人已接受，授权本地提交）

提交前复查：pytest **462 passed in 7.56s**；ruff check、ruff format --check、git diff --check 均通过。

- **完成项**：runs/tool_events 持久化、`audit --limit`、固定字段与摘要上限、问题仅哈希/字符数、
  CLI 与工具正常/拒绝/异常记录、模型用量逐次保存、清除后审计保留；预算拒绝在返回工具结果前入账。
  审计故障停止后续模型请求，异常只输出自写消息；验证提示注入不能增加工具或写源文件，tracing 保持关闭。
- **文件**：新增 `src/workpilot/security/audit.py`、`tests/test_audit.py`；更新仓储、CLI、Agent 回路、
  project_tools、sync、六个既有测试文件、README 和本页。schema、依赖锁、规格、AGENTS、原始 fixture 未改。
- **测试结果（已执行，macOS / Python 3.12.14）**：`.venv/bin/python -m pytest -q` **462 passed in 7.89s**
  （较 T04 新增 49 项）；`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`（57 files）、
  `git diff --check` 均通过。真实 SQLite 故障/写锁、部分用量、中断/未完成记录、越权与预算拒绝均覆盖；
  两种 SDK 导入顺序在独立禁网进程证明 NoOpTrace。Agent/SDK 仍使用 StubModel/MockTransport。
  已安装 CLI 完成 **10 步离线演示**，得到 8 条 run、1 条 tool event；审计全部字段无 canary、假 Key、
  问题/正文或来源绝对路径，schema 1、权限 0700/0600、源文件哈希不变；规格/AGENTS/原始 fixture 哈希一致。
- **风险**：审计收尾失败时，本地操作可能已提交，需检查状态；用量仅覆盖收到的有效响应。
  引用语义仍需人工核对；T04 记录的真实服务、其他平台与独立新环境验证限制保持不变。
- **未做事项**：未推送/合并，未实施 T06；未读取真实公司资料/密钥，
  未运行外部业务操作、外发业务文档或调用真实付费模型；本轮未更新依赖。
- **下一任务**：按负责人授权完成 T05 本地提交，然后只实施 T06 回归验收与交付文档。
