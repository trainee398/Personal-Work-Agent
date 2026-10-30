# WorkPilot 任务状态

当前：**T04 已接受，负责人授权本地提交，随后只实施 T05。**
提交前分支 `main`，HEAD 为 `35f052e`，比本地跟踪引用 `origin/main` 超前 1 个提交；未推送或合并。
本轮先完成 T04 本地提交，再进入 T05。唯一规格：[V0_1_SPEC.md](V0_1_SPEC.md)。

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

## 交接记录

历史完整六项交接保留在提交快照：
[T00 · 1369946](https://github.com/trainee398/Personal-Work-Agent/blob/1369946422bf3830f29d5874c73c01636bd54718/docs/STATUS.md)；
[T01 · 9cee427](https://github.com/trainee398/Personal-Work-Agent/blob/9cee427/docs/STATUS.md)；
[T02 与既有裁决 · 1554524](https://github.com/trainee398/Personal-Work-Agent/blob/1554524/docs/STATUS.md)。
T01 实现为 `f52c759`；T02 实现为 `e897e61`。T03 已按授权提交 `35f052e`，完整交接可本地查看
`git show 35f052e:docs/STATUS.md`；提交前 **296 passed in 2.37s**，ruff、格式、diff 检查通过。

### 2026-10-30 · T04 只读 Agent 问答（负责人已接受，授权本地提交）

提交前复查：pytest **413 passed in 4.59s**；ruff check、ruff format --check、git diff --check 均通过。

- **完成项**：ModelClient/OpenAI 适配/StubModel、三个只读工具、ask 与零网络 dry-run、冻结材料与
  逐轮授权复核、引用强校验/来源时间/人工记忆区分、轮次/字符/双超时/输出预算及安全错误码。
  固定 `openai-agents==0.24.0`（`openai 3.41.0`、`httpx2 2.16.0`），仅适配官方 Responses；
  设置 `store=false`，关闭 tracing/自动重试/重定向/环境代理；doctor 通过 NoOpTrace 离线证明。
- **文件**：新增 `src/workpilot/{llm,agent,tools}/` 和 `tests/test_{project_tools,citation,agent_contract,ask_cli,llm}.py`；
  更新 CLI、包入口、doctor、共享 fixture、test_cli/test_sync、pyproject/uv.lock、README 和本页。
  schema、规格、AGENTS 与原始 fixture 未改。
- **测试结果（已执行，macOS / Python 3.12.14）**：`.venv/bin/python -m pytest -q` **413 passed in 6.97s**；
  `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`（55 files）、`git diff --check` 均通过。
  T04 五个测试文件单独运行 **116 passed in 2.60s**；回路用 StubModel，SDK 用 MockTransport，未连接真实模型。
  已安装 CLI 在临时虚构项目完成 **9 步离线演示**：帮助、登记/同步、预览、未许可 4、缺配置 6、
  无命中返回未知、doctor NoOpTrace；每个进程拦截 DNS/TCP。源文件及上述不可变文件哈希核验一致。
- **风险**：保守引用检查可能拒绝可读但格式不符的回答，仍需人工核对语义；固定候选范围可能降低召回。
  真实 OpenAI 可达性/模型权限/付费问答、其他供应商、Python 3.11、Linux/Windows 与独立新环境未验证。
- **未做事项**：未推送/合并，未实施 T05 审计写入或 T06；未读取真实公司资料/密钥，
  未运行外部业务操作、外发业务文档或调用真实付费模型。仅下载并安装了公开 SDK 依赖。
- **下一任务**：按负责人授权完成 T04 本地提交，然后只实施 T05 本地审计与安全收口。
