# WorkPilot（Personal Work Agent）

本地个人工作助手，当前仅完成 V0.1 的 **T00 项目骨架**。
已提供可安装的 Python 包、Typer 帮助入口和离线测试框架。
项目注册、数据库、同步、问答等功能按后续任务实施，当前尚不可用。

项目仓库：[trainee398/Personal-Work-Agent](https://github.com/trainee398/Personal-Work-Agent)。

唯一规格：[docs/V0_1_SPEC.md](docs/V0_1_SPEC.md)。
当前交接：[docs/STATUS.md](docs/STATUS.md)。
协作边界：[AGENTS.md](AGENTS.md)。

## 安装（Python >=3.11）

本机默认 `python3` 为 **3.9.6，不满足要求**。推荐用 uv 安装独立的 Python 3.12，
不替换系统 Python。以下命令在本项目根目录运行；已安装 uv 时跳过第一行。

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

首次安装 Python 和依赖需要访问公开软件分发源。`uv sync --locked` 在项目内创建 `.venv/`
并安装开发依赖；安装完成后的以下检查完全使用本地环境，无需 API Key。

## 本地检查

```bash
uv run --offline --no-sync workpilot --help
uv run --offline --no-sync pytest
uv run --offline --no-sync ruff check .
uv run --offline --no-sync ruff format --check .
```

也可激活 `.venv` 后直接执行 `workpilot --help`、`pytest` 和 `ruff check .`。
当前无参数运行显示帮助；未知命令返回 Typer 用法错误（退出码 2）。
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

T00 未引入 `openai-agents`。T04 引入时必须选择并固定经验证的具体版本、记录到 README，
并将 SDK 隔离在 `llm/` 的实现中；当前不预设未经验证的 SDK 版本。

- `src/workpilot/`：包初始化和 CLI 帮助入口。
- `tests/`：CLI 冒烟检查、离线测试配置及 fixture 完整性检查。
- `tests/fixtures/demo_project/`：纯虚构中文 Markdown、无日期小节、提示注入文本、
  `CANARY-DO-NOT-LEAK`、CRLF 变体和假 `.env`。
- `.workpilot/`：被忽略的开发缓存或可丢弃数据；`.venv/`、数据库和真实 `.env*` 也被忽略。
  仅 `.env.example` 与指定路径下的虚构 `.env` 例外。

Fixture 的用途见 [tests/fixtures/README.md](tests/fixtures/README.md)。
它们不代表任何真实项目，不得把其中的注入文本当作指令执行。
后续模块与测试文件在对应 Txx 建立。

## 当前配置与边界

`.env.example` 仅说明后续配置接口，T00 不加载 `.env`，不读取项目源文件或创建运行数据库。
未来运行数据默认位于 `~/.local/share/workpilot/`，通过 `WORKPILOT_DATA_DIR` 覆盖；
该配置及权限管理属于 T01。运行数据不得放入业务仓库。

包初始化时强制设置 `OPENAI_AGENTS_DISABLE_TRACING=1`；当前不安装或导入模型 SDK。
SDK 运行时关闭和 `doctor` 的完整离线证明留待后续任务。
本项目不修改 QIYE-IM 等业务项目，不扫描敏感目录，不执行外部业务操作或真实模型调用。

macOS 为首要目标，Linux 兼容性待验证，Windows 在 V0.1 中不作承诺。
