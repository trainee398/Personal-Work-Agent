# WorkPilot 任务状态

当前：T00、T01 已完成；T02 未开始。唯一规格：`docs/V0_1_SPEC.md`。
仓库：`https://github.com/trainee398/Personal-Work-Agent.git`；维护分支为 `origin/main`，T01 实现提交见下文。

规格原样迁移自 `docs/WORKPILOT_V0_1_SPEC.md`，36042 字节，SHA-256：
`cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb`（T01 后再次验证未变）。

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
