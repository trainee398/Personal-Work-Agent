# WorkPilot 任务状态

当前：T00、T01 已发布；T02 已接受并本地提交 `e897e61`；T03 实施中，本次不推送。唯一规格：`docs/V0_1_SPEC.md`。
仓库：`https://github.com/trainee398/Personal-Work-Agent.git`；T01 实现 `f52c759`、交接提交 `9cee427` 已核验与远端一致。

规格原样迁移自 `docs/WORKPILOT_V0_1_SPEC.md`，36042 字节，SHA-256：
`cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb`（T02 后再次验证未变）。

## 规格疑问

- **负责人已裁决，2026-10-30**：认可 SDK 固定版本与 tracing 现状；T04 引入 SDK 时固定
  已验证版本，tracing 保持强制设为 `1`；§2 措辞由负责人统一，实现者不修改规格。
- **负责人已裁决，2026-10-30**：认可 `projects.allowed_files_json`，保持现实现；
  不借未索引的 `sources` 行伪造哈希或时间，仍为七张表。
- **负责人已裁决，2026-10-30**：认可 `projects.active` 和现有移除/彻底清除语义；
  普通移除停用来源、删除 chunks、保留记忆与名称；确认清除后删除登记/来源/记忆，
  审计元数据保留并解除关联。
- **负责人已裁决，2026-10-30**：认可 `doctor` 只检查、不建库、不迁移、不修复；
  未初始化返回 6，FTS5 缺失与锁占用仅提示，保持现实现。
- **T02 阶段实现已接受，2026-10-30**：长段续块无法同时重复覆盖原始标题行并满足连续行号/字符上限；首块保留
  实际标题行，续块保留面包屑、引用其真实连续原始行，不复制标题或虚构行号。超长段先按段落、
  必要时按完整行切分；单行超过 2000 字符则拒绝（4），同标题内的小块合并不突破上限。
- **T02 阶段实现已接受，2026-10-30**：缺失、撤权或读取安全校验失败均停用旧来源并删除 chunks；首次读取即缺失不造
  空哈希的来源行。存储事务失败仍按 §6.4 保留旧哈希/切片并报错；审计写入留 T05。
  读取期间变化按资源忙返回 7，清理该来源旧切片后停止本次同步；稳定后可重新同步。
- **T03 待复核**：现有 schema 没有项目级同步尝试时间；status 的“上次同步时间”取已记录
  来源最大的 indexed_at，并明确标注其口径；无记录显示“尚无已记录的来源同步时间”，不新增表或伪造时间。
  来源缺失/未索引/过期作为正常报告告警（0）；项目不存在为 3，策略拒绝为 4，配置错误为 6。
  从未索引的白名单文件展示 unindexed 或 missing 和空元数据；摘录只来自已授权、可安全检查的
  active 缓存，并标注“在上次同步时读取到”；缺失、撤权或 stat 已变更时不展示旧正文。

## 交接记录

### 2026-10-30 · T00

- **完成项**：包/CLI/依赖锁、协作文档、全部纯虚构 fixture；原始完整交接见
  [T00 发布快照](https://github.com/trainee398/Personal-Work-Agent/blob/1369946422bf3830f29d5874c73c01636bd54718/docs/STATUS.md)。
- **文件**：共 19 个文件，完整清单见上述快照。
- **测试结果**：新环境安装、CLI 帮助、8 项 pytest、ruff 检查与格式检查通过。
- **风险**：当时默认 Python 3.9.6、Git 自动发现异常，已在下面的交接中解决。
- **未做事项**：当时未做 T01–T06、未配置或推送远端，详见快照中的历史记录。
- **下一任务**：T01，已按用户后续指令完成。

### 2026-10-30 · 发布、Python 升级及 T01

T01 实现提交：`f52c759d535fd7a9184bb6c890c2787c46f31e4a`（负责人裁决已纳入）。

- **完成项**：按用户顺序将 T00 提交并推送 `origin/main`，远端核验为
  `1369946422bf3830f29d5874c73c01636bd54718`；用户级 `python/python3` 升级为 3.12.14，
  新 shell 验证通过。随后完成 T01：配置/默认值、SQLite 幂等迁移、事务/约束/权限、项目
  登记/列表/移除/确认清除、离线 doctor 和错误退出码；普通 Git 自动发现现已正常。
- **文件**：新增 `src/workpilot/{config,errors,projects,doctor}.py`、`domain/{__init__,models}.py`、
  `storage/{__init__,db,repositories}.py`；新增 `tests/test_{config,storage,projects,doctor}.py`；
  更新 CLI、conftest/test_cli、README、AGENTS、pyproject、`.env.example` 和本页。
  用户级默认入口为 `~/.local/bin/{python,python3}`；原有业务项目及虚拟环境未改动。
- **测试结果（已执行）**：`uv --no-config --cache-dir .workpilot/uv-cache sync --locked --offline --python 3.12 --no-python-downloads --no-progress` 安装更新通过；
  `.venv/bin/python -m pytest -q`：**95 passed**；`.venv/bin/ruff check .`、
  `.venv/bin/ruff format --check .`、`git diff --check` 均通过。安装后的 CLI 在临时虚构项目
  完成 11 步流程（含重复初始化、重名拒绝、诊断、取消/确认清除）；SQLite 3.53.1、schema 1、
  目录/数据库 0700/0600 均符合预期。规格、原 fixture 和演示源文件哈希未变。
- **风险**：四条规格疑问已由负责人裁决；Python 3.11 与 Linux/Windows 未实测。
  收尾复核：pytest 95 项、ruff check、ruff format --check、git diff --check 均通过。
- **未做事项**：未实施 T02–T06、全文读取/索引、Agent 或审计写入；未访问真实公司文件、
  敏感目录、外部业务系统或真实付费模型；未合并分支。源读取的完整安全策略仍由 T02 验收。
- **下一任务**：获指派后只做 T02；先复核本页、规格、AGENTS 和 Git 状态。

### 2026-10-30 · T02（负责人已接受，授权本地提交）

本地提交：`e897e6184af3b7840847526e9ce0afa15da3cf34`（`feat: add WorkPilot T02 read-only Markdown indexing`）。
提交前复查：pytest **229 passed in 1.68s**；ruff check、ruff format --check、git diff --check 均通过。
提交后工作区干净；未推送。GitHub 只读查询因连接超时失败，本地 origin/main 记录仍为 `9cee427`。

- **完成项**：读取时重新授权、硬拒绝清单、realpath + `O_NOFOLLOW` 和前后身份校验；
  原始字节/归一化正文双哈希、确定性切分与 bigram 检索；`sync <项目名>` 幂等增量更新、
  逐来源原子事务、缺失/撤权清理、重现后激活；跨项目非阻塞 `fcntl` 锁及退出码 7。
- **文件**：共 19 个本地变更文件。新增 `ingestion/{__init__,markdown,sync}.py`、
  `security/{__init__,policy}.py`、`retrieval.py`、`storage/locking.py`（均在 `src/workpilot/`）；
  更新 CLI、domain/models、projects、storage/repositories；新增
  `tests/test_{markdown,security,sync,sync_lock,retrieval}.py`，补充 conftest；更新 README 和本页。
  沿用 T01 schema、依赖与架构；原有六个测试文件、全部原始 fixture、规格和依赖配置未改。
- **测试结果（已执行，macOS / Python 3.12.14）**：`.venv/bin/python -m pytest -q`：
  **229 passed in 1.65s**（原有 95 项 + 新增 134 项）；`.venv/bin/ruff check .`：通过；
  `.venv/bin/ruff format --check .`：**37 files already formatted**；`git diff --check`：通过。
  已安装的 `.venv/bin/workpilot` 在临时虚构项目完成 **12 步 CLI 演示**：帮助、初始化、登记、
  两次 `sync fictional`、doctor、错误的 `sync --project fictional`（2）、缺失同步（3）、
  恢复同步、移除、移除后同步（3）、doctor；切片数 **12 → 6 → 12 → 0**，只读操作前后源文件
  哈希一致，正文/canary 未出现在输出。pytest 另验证双进程锁竞争立即返回 7、事务中途失败
  完整回滚、读取竞态与中文检索排序；规格和原始 fixture 哈希保持不变。
- **风险**：长段续块的标题/行号解释、单行超过 2000 字符返回 4、失效处理边界见上方
  “规格疑问”；负责人已接受阶段实现，规格正文仍由负责人统一；Python 3.11、Linux、Windows 未实测。
- **未做事项**：验收时 T02 尚未提交或推送，基于 `9cee427`；未实现 T03–T06、status/memory/
  ask/audit、模型工具或审计写入；未访问真实公司文件、敏感目录、业务系统或付费模型。
- **下一任务**：负责人已于 2026-10-30 接受 T02；复查通过后只做本地提交，随后实现 T03。
