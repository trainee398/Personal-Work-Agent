# WorkPilot（Personal Work Agent）

本地个人工作助手，当前实现到 V0.1 的 **T03 离线状态与人工记忆**，负责人已接受阶段成果。
可登记明确授权的项目文件、增量同步 Markdown、离线查看带来源的摘录、记录人工记忆，
并执行项目移除和环境自检。问答和审计命令留待 T04–T05。

项目仓库：[trainee398/Personal-Work-Agent](https://github.com/trainee398/Personal-Work-Agent)。

唯一规格：[docs/V0_1_SPEC.md](docs/V0_1_SPEC.md)。
当前交接：[docs/STATUS.md](docs/STATUS.md)。
协作边界：[AGENTS.md](AGENTS.md)。

## 安装（Python >=3.11）

本机原先默认 `python3` 为 **3.9.6，不满足要求**。现已按用户授权将用户级
`python` / `python3` 配置为 **3.12.14**，系统解释器未替换。
新环境推荐用 uv 安装 Python 3.12。以下命令在项目根目录运行；已有 uv 时跳过第一行。

```bash
brew install uv
uv python install 3.12
uv sync --locked --python 3.12
uv run --offline --no-sync python --version
uv run --offline --no-sync workpilot --help
```

没有 Homebrew 时可按 [uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)
安装 uv，再执行后续命令。已有 Python 3.11+ 时，也可把 `--python 3.12` 换成该解释器的
绝对路径。`.python-version` 选择推荐的 3.12；包的最低版本由 `requires-python = ">=3.11"`
约束。

若希望同时设置用户级的 `python` / `python3` 默认入口，可运行
`uv python install 3.12 --default`（本机已执行）；要求 `~/.local/bin` 位于系统目录之前。
更改后在新 shell 运行 `python3 --version` 检查。已有项目的虚拟环境不随默认入口改变。

首次安装 Python 和依赖需要访问公开软件分发源。`uv sync --locked` 在项目内创建 `.venv/`
并安装开发依赖；安装完成后的以下检查完全使用本地环境，无需 API Key。

## 本地使用示例（纯虚构数据）

```bash
export WORKPILOT_DATA_DIR="$PWD/.workpilot/demo-data"
uv run --offline --no-sync workpilot init
uv run --offline --no-sync workpilot project add demo --root ./tests/fixtures/demo_project --file STATUS.md
uv run --offline --no-sync workpilot project list
uv run --offline --no-sync workpilot status demo
uv run --offline --no-sync workpilot sync demo
uv run --offline --no-sync workpilot sync demo
uv run --offline --no-sync workpilot status demo
uv run --offline --no-sync workpilot memory add demo --kind decision --text "纯虚构：本轮先完成离线验收"
uv run --offline --no-sync workpilot memory list demo
uv run --offline --no-sync workpilot memory list demo --kind decision
uv run --offline --no-sync workpilot doctor
uv run --offline --no-sync workpilot project remove demo
uv run --offline --no-sync workpilot project remove demo --purge
```

`init` 可重复执行。登记仅检查项目根目录和指定文件的元数据，保存授权清单；不读取正文，
也不会提前生成来源哈希或索引。`--file` 可重复指定，清单去重并排序；项目名必须唯一。
根目录不能包含 `..`；文件必须是项目根内的相对 `.md` / `.markdown` 普通文件。
敏感文件名、越界路径和最终组件为符号链接的文件会被拒绝。
`sync demo` 才会读取正文；命令使用位置参数项目名，不接受 `sync --project demo`。

普通 `remove` 取消登记，清除旧索引正文，保留人工记忆、来源元数据和项目名。
`--purge` 需要交互确认，清除项目登记、索引和人工记忆；已有审计元数据保留并解除项目关联。
输入 `n` 或直接回车会取消；无交互输入则返回 2。已移除的名称仍保留，需确认彻底清除后才可重用。
运行数据与源项目目录必须分开，任何移除操作都不删除源文件。

## 只读同步与确定性检索

每次读取重新核对项目仍在登记、文件在显式白名单内，以及 `realpath` 位于项目根内。
读取用只读文件描述符，逐级固定解析后的目录，最终组件使用 `O_NOFOLLOW`；读取前后核对
文件身份、大小、mtime、ctime 和授权状态。最终符号链接（含悬空链接）、硬链接、目录、
FIFO 等特殊文件均拒绝；父目录别名仅在解析后仍处于项目根内且未命中拒绝清单时允许。
`.env*`、密钥文件、credential、`.ssh`、`.git` 等硬拒绝项无法通过显式授权绕过。
不会扫描未登记的文件，也不会执行文档里的指令。

来源必须是 UTF-8 的 `.md` / `.markdown` 文件，原始字节最多 1 MiB；二进制 NUL 拒绝。
BOM 在入库前去除，CRLF / CR 归一化为 LF。来源 SHA-256 使用原始字节，切片哈希使用
归一化后的块正文。重复同步或仅修改 mtime 不重建切片，只刷新来源的时间等元数据。
原始字节改变时，在单个事务内替换该来源的全部切片；写入失败回滚，保留旧哈希和旧切片。

切片按 1–3 级 ATX 标题分段，保留标题面包屑和 1 起、闭区间的真实行号；无标题文档按段落
切分。块上限 2000 字符，同标题段内小于 200 字符的相邻块在不超上限时合并。长段先按
空行、再按完整行切分；续块保留标题面包屑，正文和行号只涵盖其实际连续行，不复制标题行。
单个物理行连同换行符超过 2000 字符时返回 4。该边界解释已记入 STATUS 的“规格疑问”，
阶段实现已由负责人接受，规格正文仍由负责人统一。空文件成功生成 0 块，并显示告警。

文件缺失、撤销文件授权或读取安全校验失败时，旧来源停用且删除其全部切片正文；首次读取
即缺失不会伪造来源哈希。来源重新出现并通过校验后重新激活、重建切片。读取过程中变化的
文件失败关闭并返回 7，需重新同步。每个文件独立提交，某个文件缺失或被拒绝时仍处理其余
授权文件；CLI 输出计数和告警，不输出文档正文。

`sync` 对数据目录中的 `workpilot.lock` 加非阻塞 `fcntl.flock` 独占锁，覆盖所有项目；
锁占用时立即返回 7，不读取源文件。完成或异常均释放锁，锁文件本身保留，以维持同一 inode。

T02 提供内部检索函数 `workpilot.retrieval.search_sources`，暂不增加检索 CLI 或 Agent 工具。
它在同一 SQLite 读取快照内筛选仍授权的 active 来源：NFKC、ASCII 小写，ASCII 按词、
中日韩连续文字按双字滑窗分词；查询词去重后按标题命中 ×3、正文出现次数封顶 3 打分。
同分依次按相对路径、起始行排序，最多返回 8 块，摘录连同省略号不超过 400 字符。
空查询、单个中日韩字或零命中返回空列表；来源被删除后的旧证据在下次同步清理后不可检索。

## 离线状态与人工记忆

`status demo` 无需 API Key 或模型名，不调用网络或模型，不读取源文件正文，不触发同步。
它在同一 SQLite 读取快照内取得来源元数据和摘录，并复用安全文件描述符检查当前文件的
授权、路径、类型、大小和 mtime。固定输出顺序为项目名/根目录、上次同步时间、按相对路径
排序的来源表、告警；随后展示文件证据摘录。输出为纯文本，不随终端宽度截断来源信息。

来源表包含状态、SHA-256 前 8 位、已存切片的最大行号、`indexed_at` 和过期标记。
`active` 表示索引仍启用，`missing` 表示当前文件缺失，`unindexed` 表示尚未索引，
`inactive` 表示旧索引已停用。未索引文件的哈希、行数和时间显示为空；索引为空明确提示
“无已索引资料”。数据库未保存项目级同步尝试时间，因此“上次同步时间”明确取已记录来源
最大的 `indexed_at`；没有来源记录时不会以项目登记时间代替。

每个可用来源展示首个切片的最多 400 字符，保留 `[相对路径:Lx-Ly]` 与索引时间，并注明
“在上次同步时读取到”。超过 `WORKPILOT_STALE_DAYS`（默认 7 天，严格大于才算过期）
会告警；文件缺失、来源失效或检测到 mtime/size 变化时隐藏旧摘录。已撤权来源不展示正文，
也不再探测该文件；清理旧切片仍由 `sync` 完成。缺失/未索引/过期属于正常报告（退出码 0），
项目不存在为 3，路径策略拒绝为 4，检查期间文件变化为 7；失败时不输出部分摘录。
元数据检查无法发现大小与已记录的秒级 mtime 都未变化的内容改写；`sync` 会校验原始字节哈希。

`memory add` 必须显式指定 `--kind decision|note|preference` 和非空的 `--text`。
记忆仅写入本地 `memory_entries`，来源固定为 `manual`；保留输入正文和 UTC 创建/更新时间。
`memory list` 可按 `--kind` 筛选，按创建时间、ID 升序输出，并标注“人工记忆，不是项目进度证据”。
重复输入会新增独立记录；文件资料缺失时仍可记录笔记，但 `status` 不会用笔记补造项目进度。
终端控制字符以可见转义形式展示，正文存储不变；文档和记忆中的指令只作为数据处理。

## 本地数据与 doctor

未设置 `WORKPILOT_DATA_DIR` 时，数据位于 `~/.local/share/workpilot/workpilot.db`。
目录权限为 0700，数据库和 SQLite 附属文件为 0600；每次连接开启外键检查与 WAL。
普通数据命令会按需初始化并检查 schema；迁移在事务中执行，当前为版本 1。
未知版本、缺少版本信息的既有数据库不会被覆盖或降级。

`doctor` 完全离线检查解释器、SQLite/FTS5、配置、目录和数据库权限、完整性、schema、
表计数、tracing 环境变量及锁状态。它不自动初始化、迁移或修复，以便如实暴露问题。
新环境未初始化时返回 6，先运行 `init`；FTS5 不可用或锁正被占用仅作提示，不等待锁。
API Key 只显示“已设置/未设置”。普通本地命令不需要 API Key 或模型名。

`.env.example` 仅列出配置接口，程序不自动读取 `.env`。
`WORKPILOT_SEND_TO_MODEL` 仅接受 `true` / `false`，默认 `false`；
`WORKPILOT_STALE_DAYS` 为正整数，默认 7。附录 B 默认值集中在 `config.py`；
T02/T03 已使用文件、切分、检索、锁和过期参数，模型预算留待后续任务。

| 退出码 | 当前含义 |
| --- | --- |
| 0 | 成功、状态报告告警，或用户取消清除 |
| 2 | 用法错误、重复名称、无效根目录、空白记忆、清除未收到确认 |
| 3 | 项目或授权文件不存在 |
| 4 | 白名单、路径、文件类型、大小、编码或切片限制拒绝 |
| 6 | 配置、权限、数据库或自检错误 |
| 7 | 同步锁冲突、数据库资源忙或源文件读取/检查期间变化 |

## 本地检查

```bash
uv run --offline --no-sync workpilot --help
uv run --offline --no-sync pytest
uv run --offline --no-sync ruff check .
uv run --offline --no-sync ruff format --check .
```

也可激活 `.venv` 后直接执行 `workpilot --help`、`pytest` 和 `ruff check .`。
无参数运行显示帮助；未知命令返回 Typer 用法错误（退出码 2）。
测试使用临时数据目录、清除模型配置，并拦截 Python DNS/TCP 连接。
T02 验收集中在 `test_markdown.py`、`test_security.py`、`test_sync.py`、`test_sync_lock.py`
和 `test_retrieval.py`：包含真实 SQLite 事务故障、真实双进程锁竞争和虚构文件替换竞态。
T03 验收位于 `test_status.py`、`test_memory.py`，覆盖确定性来源报告、过期边界、缺失/撤权、
元数据检查竞态、SQLite 读取快照、人工记忆持久化/隔离/回滚及源文件哈希不变。
实际执行命令与计数见 [docs/STATUS.md](docs/STATUS.md)。

## 依赖与结构

`pyproject.toml` 约束依赖主版本，`uv.lock` 记录解析后的精确版本及分发文件校验值。
使用 Typer、Rich、Pydantic v2，开发检查使用 pytest 和 ruff，构建使用 Hatchling。

本次在 macOS / Python 3.12.14 上验证的直接依赖如下；完整解析版本以 `uv.lock` 为准。

| 依赖 | 锁定并验证的版本 |
| --- | --- |
| Typer | 0.27.3 |
| Rich | 14.3.4 |
| Pydantic | 2.14.2 |
| pytest | 8.4.2 |
| ruff | 0.17.3 |

当前未引入 `openai-agents`。T04 引入时必须选择并固定经验证的具体版本、记录到 README，
并将 SDK 隔离在 `llm/` 的实现中；当前不预设未经验证的 SDK 版本。

- `src/workpilot/`：CLI、配置、项目登记、SQLite 存储与锁、离线诊断，以及
  `security/` 安全读取、`ingestion/` 归一化/切分/同步、`retrieval.py` 确定性检索、
  `status.py` 离线报告和 `memory.py` 人工记忆。
- `tests/`：T00–T03 验收；全部使用纯虚构资料与临时数据目录。
- `tests/fixtures/demo_project/`：纯虚构中文 Markdown、无日期小节、提示注入文本、
  `CANARY-DO-NOT-LEAK`、CRLF 变体和假 `.env`。
- `.workpilot/`：被忽略的开发缓存或可丢弃数据；`.venv/`、数据库和真实 `.env*` 也被忽略。
  仅 `.env.example` 与指定路径下的虚构 `.env` 例外。

Fixture 的用途见 [tests/fixtures/README.md](tests/fixtures/README.md)。
它们不代表任何真实项目，不得把其中的注入文本当作指令执行。
后续模块与测试文件在对应 Txx 建立，规格歧义的当前处理见 `docs/STATUS.md`。

## 当前边界

包初始化时强制设置 `OPENAI_AGENTS_DISABLE_TRACING=1`；当前不安装或导入模型 SDK。
SDK 运行时关闭的完整证明留待 T04/T05，当前 `doctor` 明确区分环境变量与 SDK 状态。
本项目不修改 QIYE-IM 等业务项目，不扫描敏感目录，不执行外部业务操作或真实模型调用。

macOS 为首要目标，Linux 兼容性待验证，Windows 在 V0.1 中不作承诺。
