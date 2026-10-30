# WorkPilot（Personal Work Agent）

本地个人工作助手，当前实现到 V0.1 的 **T04 只读 Agent 问答**，负责人已接受阶段成果。
可登记明确授权的项目文件、增量同步 Markdown、离线查看带来源的摘录、记录人工记忆，
并在明确允许外发后进行有引用的问答。`ask --dry-run` 可离线预览材料范围；本地审计留待 T05。

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
uv run --offline --no-sync workpilot ask demo "当前主要风险是什么？" --dry-run
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

内部检索函数 `workpilot.retrieval.search_sources` 提供确定性检索；T04 工具复用同一分词与打分规则。
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

## 只读问答与外发预览

对已登记且完成 `sync` 的项目，先查看本次问题的候选材料清单：

```bash
.venv/bin/workpilot ask demo "当前主要风险是什么？" --dry-run
```

预览无需 API Key、模型名或外发许可，不导入模型 SDK、不创建 API 客户端、不联网。
它只显示问题、来源概要、文件切片及人工记忆的类别、相对路径或记忆 ID、行号、字符数和
估算 tokens，不打印问题原文、标题、摘录或记忆正文。tokens 按 UTF-8 字节数除以 3 向上取整，
仅用于粗略估算，不代表服务端实际计费。

每次提问在本地冻结工具可返回的范围：可用来源的一二级标题及元数据、原问题命中的最多
8 个切片（每个摘录含省略号最多 400 字符），以及本项目的人工记忆。实际问答可能只使用
其中一部分；模型不能通过改写查询扩大到其他切片或项目。新一次 `ask` 会重新选择和检查材料，
`dry-run` 不保存外发许可。已缺失、停用、撤权或检测到 mtime/size 变化的旧内容不纳入候选。

确需外发时，通过环境注入自己的 `OPENAI_API_KEY` 和 `WORKPILOT_MODEL`，再明确授权当次问答：

```bash
.venv/bin/workpilot ask demo "当前主要风险是什么？" --allow-remote
```

模型名无默认值，须支持官方 Responses API 的 function tools。`--allow-remote` 仅本次生效；
显式设置 `WORKPILOT_SEND_TO_MODEL=true` 等价于为该环境中的每次 `ask` 授权，程序不会替用户
修改此设置。`--dry-run` 始终优先，即使同时带许可或设置了上述环境变量也不调用模型。
无许可返回 4，缺 Key 或模型名返回 6；无命中文件证据时输出“现有索引不足以回答该问题”，
正常结束且不创建客户端。人工记忆不能填补文件证据空缺。

三个只读工具为 `get_project_overview`、`search_project_sources`、`list_project_memories`。
工具参数绑定本次项目，拒绝任意路径、额外字段、跨项目访问及其他能力；每次工具调用与
模型请求前重新核验已选材料，避免在撤权后继续发送历史内容。只有实际返回给模型的摘录
才能作为回答证据，人工记忆另列并注明“人工记忆，不是项目进度证据”。

每个事实句须附 `[相对路径:Lx-Ly]`。程序校验真实路径、当前授权和 active 来源、切片身份与
哈希、完整行号范围，以及该范围是否已在工具摘录中返回；截断后完全未发送的后续行不可引用。
混有无效引用时在顶部警示并列出条目；没有有效引用或存在无引用句时失败关闭，不展示模型原文。
回答末尾固定列出来源和 `indexed_at`；注明资料来自上次同步，过期索引或最终生成期间检测到
文件 stat 变化会告警。引用校验能证明出处与范围，不能证明句意一定由摘录支持，仍需人工核对。

每次问答最多 6 个工具轮次，随后只允许一个禁用工具的收尾回合；单次模型请求最长 60 秒，
整次问答预算 120 秒，每次模型输出最多 2000 tokens。候选材料与每次序列化上下文均检查
12000 字符上限（上下文含问题、系统提示、工具 schema、调用和历史结果）；超大工具结果不
加入历史，并停止继续取材。候选材料过大返回 4；网络、超时或非法模型响应返回 5，错误脱敏。

目前仅适配 `https://api.openai.com/v1` 的 Responses API，忽略 `OPENAI_BASE_URL`，不跟随
重定向、不自动重试、不使用环境代理；设定 `store=false`，不启用会话或自动工具执行。
官方 SDK 协议已用离线 HTTP 替身验证，真实服务可达性、模型权限与实际付费问答尚未验证。

## 本地数据与 doctor

未设置 `WORKPILOT_DATA_DIR` 时，数据位于 `~/.local/share/workpilot/workpilot.db`。
目录权限为 0700，数据库和 SQLite 附属文件为 0600；每次连接开启外键检查与 WAL。
普通数据命令会按需初始化并检查 schema；迁移在事务中执行，当前为版本 1。
未知版本、缺少版本信息的既有数据库不会被覆盖或降级。

`doctor` 完全离线检查解释器、SQLite/FTS5、配置、目录和数据库权限、完整性、schema、
表计数、tracing 环境变量与 SDK 运行时关闭状态及锁状态。它不自动初始化、迁移或修复，以便如实暴露问题。
新环境未初始化时返回 6，先运行 `init`；FTS5 不可用或锁正被占用仅作提示，不等待锁。
API Key 只显示“已设置/未设置”。普通本地命令不需要 API Key 或模型名。

`.env.example` 仅列出配置接口，程序不自动读取 `.env`。
`WORKPILOT_SEND_TO_MODEL` 仅接受 `true` / `false`，默认 `false`；
`WORKPILOT_STALE_DAYS` 为正整数，默认 7。附录 B 默认值集中在 `config.py`；
T02–T04 已使用文件、切分、检索、锁、过期及模型预算参数；审计摘要限制留待 T05。

| 退出码 | 当前含义 |
| --- | --- |
| 0 | 成功、状态报告告警、问答证据不足，或用户取消清除 |
| 2 | 用法错误、重复名称、无效根目录、空白记忆/问题、清除未收到确认 |
| 3 | 项目或授权文件不存在 |
| 4 | 白名单、文件校验、字符预算、工具权限拒绝，或未授权外发 |
| 5 | 模型请求失败、超时或非法模型响应 |
| 6 | 缺 Key/模型名，或配置、权限、数据库、自检错误 |
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
T04 验收位于 `test_project_tools.py`、`test_citation.py`、`test_agent_contract.py`、
`test_ask_cli.py`、`test_llm.py`，覆盖外发许可、元数据预览、只读工具、引用/撤权/预算/超时、
错误脱敏、提示注入越权拒绝，以及 SDK 导入隔离和 tracing 关闭。
Agent 回路使用 `StubModel`；SDK 适配测试使用 `httpx2.MockTransport`，不调用真实模型。
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
| openai-agents | 0.24.0（精确固定） |
| pytest | 8.4.2 |
| ruff | 0.17.3 |

SDK 的关键传递依赖为 `openai==3.41.0`、`httpx2==2.16.0`，协议与 tracing 已离线验证。
产品代码中仅 `llm/openai_client.py` 导入 Agents SDK，通过 `ModelClient` 协议接入本地
Agent 回路。适配器使用一个 SDK Agent 和 FunctionTool 定义执行单步模型请求；工具执行、
授权与预算由本地回路控制，SDK 回调不能绕过这些检查。供应商替换需负责人明确决定。

- `src/workpilot/`：CLI、配置、项目登记、SQLite 存储与锁、离线诊断，以及
  `security/` 安全读取、`ingestion/` 归一化/切分/同步、`retrieval.py` 确定性检索、
  `status.py` 离线报告、`memory.py` 人工记忆、`llm/` 模型协议与适配、`agent/` 问答/引用、
  `tools/` 三个限定范围的只读工具。
- `tests/`：T00–T04 验收；全部使用纯虚构资料与临时数据目录。
- `tests/fixtures/demo_project/`：纯虚构中文 Markdown、无日期小节、提示注入文本、
  `CANARY-DO-NOT-LEAK`、CRLF 变体和假 `.env`。
- `.workpilot/`：被忽略的开发缓存或可丢弃数据；`.venv/`、数据库和真实 `.env*` 也被忽略。
  仅 `.env.example` 与指定路径下的虚构 `.env` 例外。

Fixture 的用途见 [tests/fixtures/README.md](tests/fixtures/README.md)。
它们不代表任何真实项目，不得把其中的注入文本当作指令执行。
后续模块与测试文件在对应 Txx 建立，规格歧义的当前处理见 `docs/STATUS.md`。

## 当前边界

包初始化时在导入 SDK 前强制设置 tracing 与模型/工具数据日志关闭环境变量；SDK 适配器
再关闭运行时 tracing 并移除 trace processors。`doctor` 通过 `NoOpTrace` 离线证明关闭，
不创建 API 客户端。T04 尚不写入 `runs/tool_events`；本地脱敏审计与安全收口由 T05 交付。
本项目不修改 QIYE-IM 等业务项目，不扫描敏感目录。本轮开发验证未读取真实公司资料/密钥，
未运行外部业务操作或真实付费模型。

macOS 为首要目标，Linux 兼容性待验证，Windows 在 V0.1 中不作承诺。
