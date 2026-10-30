# WorkPilot 任务状态

当前任务：T00（已完成）；T01 尚未开始。唯一规格：`docs/V0_1_SPEC.md`。
项目仓库：`https://github.com/trainee398/Personal-Work-Agent.git`（负责人提供；本次仅记录地址）。

## 起始检查（2026-10-30）

- 显式指定 Git 目录及工作区后检查成功：`main`，尚无提交；原有规格未跟踪。
  自动仓库发现曾失败，未重建或修改 Git 元数据。没有现成的 `AGENTS.md`。
- `docs/WORKPILOT_V0_1_SPEC.md` 已原样迁移到权威路径，36042 字节；
  SHA-256：`cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb`。
- 默认 Python 3.9.6 不满足要求；本机可用 Python 3.12.14，uv 0.12.14。

## 规格疑问

- §2 要求 SDK 固定到已验证版本，T00 又明确不实现 Agent：本次不安装 SDK；
  T04 引入时验证具体版本并记录 README，当前不声称完成 SDK 验收。
- §2 的 `setdefault` 示例不会覆盖外部的 `0`，§2.1 则要求强制设为 `1`：
  T00 采用更安全的强制设为 `1`，规格原文未改；SDK 运行时关闭及 doctor 验证留后续任务。

## 交接记录

### 2026-10-30 · T00

- **完成项**：可安装的 src 布局、Typer 帮助入口、Python >=3.11 元数据、主版本约束与
  `uv.lock`、pytest/ruff 配置、协作约定与安装文档；规格原样归位。虚构 fixture 含中文、
  无日期小节、注入文本、canary、真实 CRLF 变体及假 `.env`。
- **文件**：`AGENTS.md`、`README.md`、`pyproject.toml`、`uv.lock`、`.python-version`、
  `.env.example`、`.gitignore`、`.gitattributes`、`docs/V0_1_SPEC.md`、`docs/STATUS.md`；
  `src/workpilot/{__init__,cli}.py`；`tests/{conftest,test_cli,test_fixtures}.py`；
  `tests/fixtures/README.md`、`tests/fixtures/demo_project/{STATUS.md,STATUS_CRLF.md,.env}`。
- **测试结果（已执行）**：macOS / Python 3.12.14；新建 `.venv` 安装成功。
  首次 PyPI 访问被沙箱 DNS 阻断，经批准重试成功；随后以 `uv sync --locked --offline`
  在 `.workpilot/t00-clean-venv` 新建环境安装成功（显式使用项目内缓存与 3.12 解释器）。
  `.venv/bin/workpilot --help` 返回 0，新环境入口在 `/private/tmp` 且无模型配置时也返回 0；
  `.venv/bin/python -m pytest`：**8 passed**；`.venv/bin/ruff check .` 通过；
  `.venv/bin/ruff format --check .` 通过；新环境 `uv pip check` 验证 18 个包兼容。
  规格 SHA-256 未变、仅有一个规格副本；Git 确认假 `.env` 放行、CRLF 的 `text` 属性 unset。
  验证版本：Typer 0.27.3、Rich 14.3.4、Pydantic 2.14.2、pytest 8.4.2、ruff 0.17.3。
- **风险**：Python 3.9.6 仍是系统默认，须按 README 选择 3.11+；本次未验证 Python 3.11
  或 Linux/Windows。Git 自动发现曾失败，显式 `--git-dir=.git --work-tree=.` 可检查；
  未改动其元数据。SDK 版本和 tracing 表述问题按上方“规格疑问”保留。
- **未做事项**：T01–T06 的数据库、注册、索引、检索、Agent、审计及完整安全验收均未实施；
  fixture 检查不代表已实现注入防御或硬拒绝策略。未读取真实公司文件/敏感目录、未修改业务
  项目、未运行外部业务操作或真实付费模型；未配置 Git remote、未提交、推送或合并分支。
- **下一任务**：仅在获指派后开始 T01，按规格实现本地配置、SQLite 建库/迁移、
  repositories、数据权限、项目注册与 `doctor`；先复核本页、规格、AGENTS 和 Git 状态。
