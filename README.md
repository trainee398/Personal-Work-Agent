# WorkPilot（Personal Work Agent）

本地个人工作助手，当前完成 V0.1 的 **T00 项目骨架与 T01 项目登记、本地数据层**。
可初始化 SQLite、登记明确授权的项目文件、查看或移除登记，并执行离线环境自检。
同步、状态报告、记忆命令、问答和审计命令按 T02–T05 实施，当前尚不可用。

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

## T01 使用示例（纯虚构数据）

```bash
export WORKPILOT_DATA_DIR="$PWD/.workpilot/demo-data"
uv run --offline --no-sync workpilot init
uv run --offline --no-sync workpilot project add demo --root ./tests/fixtures/demo_project --file STATUS.md
uv run --offline --no-sync workpilot project list
uv run --offline --no-sync workpilot doctor
uv run --offline --no-sync workpilot project remove demo
uv run --offline --no-sync workpilot project remove demo --purge
```

`init` 可重复执行。登记仅检查项目根目录和指定文件的元数据，保存授权清单；不读取正文，
也不会提前生成来源哈希或索引。`--file` 可重复指定，清单去重并排序；项目名必须唯一。
根目录不能包含 `..`；文件必须是项目根内的相对 `.md` / `.markdown` 普通文件。
敏感文件名、越界路径和最终组件为符号链接的文件会被拒绝。
大小、编码与读取时的完整策略检查在 T02 实现。

普通 `remove` 取消登记，清除旧索引正文，保留人工记忆、来源元数据和项目名。
`--purge` 需要交互确认，清除项目登记、索引和人工记忆；已有审计元数据保留并解除项目关联。
输入 `n` 或直接回车会取消；无交互输入则返回 2。已移除的名称仍保留，需确认彻底清除后才可重用。
运行数据与源项目目录必须分开，任何移除操作都不删除源文件。

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
`WORKPILOT_STALE_DAYS` 为正整数，默认 7。其他预算默认值集中在 `config.py`，供后续任务使用。

| 退出码 | T01 中的含义 |
| --- | --- |
| 0 | 成功，或用户取消清除 |
| 2 | 用法错误、重复名称、无效根目录、清除未收到确认 |
| 3 | 项目或授权文件不存在 |
| 4 | 路径或登记策略拒绝 |
| 6 | 配置、权限、数据库或自检错误 |
| 7 | 数据库资源忙 |

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

- `src/workpilot/`：CLI、配置、项目模型与登记、SQLite 存储和离线诊断。
- `tests/`：离线 CLI、配置、真实 SQLite 约束/迁移、权限、项目登记与移除、诊断及 fixture 检查。
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
