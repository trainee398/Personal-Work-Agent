# WorkPilot V0.1 / T06 测试报告

验收日期：2026-10-30。T05 已由负责人接受并本地提交为 `98d4716`；本报告覆盖该提交及
T06 新增的 CLI 生命周期测试、README 和交接文档。**T06 / V0.1 已由负责人验收，授权提交与推送。**
唯一需求来源为 [V0_1_SPEC.md](V0_1_SPEC.md)，未修改规格正文。

## 执行环境与结果

主机：macOS 27.0 / Apple Silicon arm64；uv 0.12.14；三套环境的 SQLite 均为 3.53.1，FTS5 可用。
独立环境指同一台机器上的新源码目录、新 `.venv` 和独立包缓存；未复用项目的 `.venv` 或包缓存。

| 验证项 | 当前项目 Python 3.12.14 | 独立环境 Python 3.12.14 | 独立环境 Python 3.11.16 |
| --- | --- | --- | --- |
| 按锁文件安装 | 沿用已有环境 | 冷缓存下载、构建并安装 51 个包，成功 | 冷缓存下载、构建并安装 51 个包，成功 |
| 全量 pytest | **463 passed in 8.76s** | **463 passed in 7.66s** | **463 passed in 7.40s** |
| UC01–UC10 独立演示 | **1 passed in 1.06s** | **1 passed in 0.96s** | **1 passed in 0.91s** |
| README 本地使用代码块 | 本轮由独立环境执行 | 逐条实际运行，退出码均为 0 | 逐条实际运行，退出码均为 0 |
| 已安装 `workpilot --help` | T05 提交前已验证 | 通过 | 通过 |
| `ruff check .` | 通过 | 通过 | 通过 |
| `ruff format --check .` | 通过 | 通过 | 通过 |
| 规格、依赖文件、原始 fixture 哈希 | 未变化 | 验证前后未变化 | 验证前后未变化 |

T05 提交前基线为 462 项测试；T06 新增 1 条完整生命周期测试，合计 463 项。
产品代码与依赖没有修改。

负责人验收后的提交前复查（2026-10-30）：当前 Python 3.12.14 **463 passed in 8.52s**，
ruff、格式、diff 和文档链接检查通过。独立环境结果沿用上表，本轮未重复安装。

当前项目执行的检查：

```bash
.venv/bin/python -m pytest -q -s tests/test_v01_acceptance.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
```

`git diff --check` 通过。独立环境使用 `uv run --offline --no-sync` 执行相同 pytest/ruff 命令，
并调用已安装的 CLI 入口。SDK 固定为 `openai-agents==0.24.0`，关键传递依赖为
`openai==3.41.0`、`httpx2==2.16.0`；其他版本见 [README](../README.md) 和 `uv.lock`。

## 独立新环境验证方法

1. 用 `git archive 98d4716` 导出已提交源码到两份任务临时目录，复制本轮 README 和新增测试。
   未复制 `.venv`、开发数据、用户配置或真实 `.env`；唯一包含的 `.env` 是已跟踪的纯虚构 fixture。
2. 对 Python 3.12 使用已安装的 3.12.14 解释器；另将 Python 3.11.16 下载到任务临时目录，
   使用 `uv python install 3.11 --install-dir <任务目录> --no-bin`，未更改默认 Python 入口。
3. 分别通过 `uv --no-config --cache-dir <独立缓存> sync --locked --python <解释器路径>`
   从公开包源完成安装。子进程使用最小环境，不继承模型凭据、代理或自定义包源配置。
4. 核对 `sys.executable`、`sys.prefix` 均指向各自的新 `.venv`，`workpilot.__file__`
   指向各自源码副本，确认没有意外导入当前项目环境。
5. 直接提取并执行 README 的“本地使用示例”代码块，包括创建临时数据目录、登记、同步两次、
   状态、人工记忆、dry-run、审计和 doctor；检查预期输出，再运行完整离线演示和全量检查。
6. 校验规格、AGENTS、依赖文件和原始 fixture 的字节哈希。全部一致。

只有 Python 与依赖安装访问了公开软件分发源。验证运行阶段不配置真实 Key，使用测试替身；
新环境的 README 演示及测试主进程另通过 `sitecustomize` 在启动时拦截 Python DNS/TCP。
测试自身的自动 fixture 也清除模型配置并拦截连接；SDK 导入顺序测试在自己的独立子进程中禁网。
这里验证的是本地 CLI 和模型协议，不包含真实 OpenAI 服务调用。

## UC01–UC10 验收映射

完整流程：[tests/test_v01_acceptance.py](../tests/test_v01_acceptance.py)。
执行 `uv run --offline --no-sync pytest -q -s tests/test_v01_acceptance.py` 可重复演示。
每次独立运行：**10/10 场景通过，38 次 CLI 调用，2 个 StubModel、4 次离线响应，
35 条 runs、11 条 tool_events**。以下各项在三套环境均通过。

| 用例 | 实际操作与断言 |
| --- | --- |
| UC01 登记 | 只授权 `STATUS.md`，登记后没有来源或切片；相邻 CRLF 文件未索引，显式指定虚构 `.env` 返回 4 |
| UC02 同步 | 首次索引 1 个来源、6 个切片；第二次未变化 1，切片 ID、标题、行号、正文与哈希完全一致；来源 SHA-256 对应原始字节 |
| UC03 状态 | 无 Key 可用；未同步时明确提示无资料；同步后两次输出一致，包含引用、索引时间和“在上次同步时读取到” |
| UC04 问答 | StubModel 经三个真实只读工具获取材料后返回有效 `STATUS.md:L9-L11` 引用；末尾有来源时间，人工记忆另列；零命中不创建模型客户端 |
| UC05 记忆 | 显式保存 decision，origin 为 manual，可分类查询；status 不混入记忆；再次 init 后索引和记忆不丢失 |
| UC06 审计 | `audit --limit 20` 可见工具及用量；扫描 runs/tool_events 全字段，无 canary、假 Key、问题、正文、回答、内部提示或来源绝对路径；问题仅存哈希与字符数 |
| UC07 变更 | 只修改临时副本；同步前旧证据隐藏，同步后旧切片被替换，回答引用新行 L2–L3；删除后来源失效、正文清空、问答证据不足；恢复后重新索引 |
| UC08 无许可/配置 | 无许可返回 4，缺 Key 或模型名返回 6，均未创建模型客户端；一次授权后再提问仍需授权，本地功能无需 Key |
| UC09 预览 | dry-run 优先于 `--allow-remote`；只输出类别、来源、行号、字符数与估算 tokens，不输出问题/摘录/记忆正文，不创建客户端 |
| UC10 自检 | 未初始化返回 6 且不建目录；初始化后返回 0，报告 Python、SQLite、权限、schema、计数、锁、NoOpTrace；不新增审计 |

附加清理验收：普通移除删除切片并停用来源，保留记忆；取消清除不删数据；确认清除后
登记、来源、切片、记忆均为空，审计 ID 与事件保留，项目关联解除。每次 CLI 调用前后
比较临时源目录全部文件哈希，最终恢复的副本与仓库原始 fixture 完全一致。

注：规格 UC02 表格写作 `sync --project demo`，第 8 节和已接受的 CLI 使用 `sync demo`；
本次沿用既有 CLI，差异记录到 [STATUS 的规格疑问](STATUS.md)。

## 全量回归覆盖

| 范围 | 已执行的主要证据 |
| --- | --- |
| 初始化、登记、存储、配置 | `test_cli`、`test_projects`、`test_storage`、`test_config`：持久化、重复名称、幂等迁移、权限、损坏/未知 schema、退出码 |
| 只读文件与索引 | `test_security`、`test_markdown`、`test_sync`、`test_sync_lock`：越界/链接/特殊文件/硬拒绝，UTF-8/BOM/CRLF/大小，原子回滚、mtime-only、真实双进程锁竞争 |
| 检索与离线状态 | `test_retrieval`、`test_status`：确定性分词/排序、中文、零命中、缺失/撤权/过期、读取快照与竞态 |
| 人工记忆 | `test_memory`：持久化、分类、隔离、回滚和源文件不变 |
| 工具与回答合同 | `test_project_tools`、`test_agent_contract`、`test_citation`、`test_ask_cli`：权限、冻结范围、有效/伪造/未返回引用、逐句校验、预算、超时、注入越权与错误脱敏 |
| SDK 与 tracing | `test_llm`：真实锁定 SDK 经 `httpx2.MockTransport` 校验 Responses 协议、端点/重定向/代理/超时/用量；两种导入顺序独立进程证明 NoOpTrace |
| 审计与诊断 | `test_audit`、`test_doctor`：全字段扫描、真实 SQLite 故障/写锁、预算拒绝、部分用量、未完成记录、清除后保留、离线检查 |
| Fixture 完整性 | `test_fixtures` 与本轮哈希复核：中文、无日期小节、提示注入、canary、CRLF、虚构 `.env` 和源文件字节不变 |

本报告未声明行覆盖率或真实模型的回答准确率；没有执行此类测量。

## 只读与范围复核

产品 `src/`、`pyproject.toml`、`uv.lock`、AGENTS 和原始 fixture 均未修改。
原始文件哈希与 T05 交接一致：

| 文件 | SHA-256 |
| --- | --- |
| `docs/V0_1_SPEC.md`（36042 字节） | `cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb` |
| `AGENTS.md` | `51c802bd172362142d5deb4970e9bc5f02e747c482663ecf9bb60b04b6d5f146` |
| fixture `STATUS.md` | `eb9e2f4803c4ef40d7328e754fcd619f08d8fbd9c7553551f7e7d1e3e30978eb` |
| fixture `STATUS_CRLF.md` | `c4a9539818fe17d1fce47b71e7f8de4f3e3a8c2d4d0ffb2a3969133c879f0625` |
| fixture `.env` | `4539a7a636eb253c4a0619a75520f1ff4005e7d56539f9c12b1a0e8b8325fecd` |

## 风险、限制与未执行项

- 真实 OpenAI 端点可达性、账号/模型权限和付费问答尚未验证；本轮没有真实公司资料或 Key，
  未执行外部业务操作或外发业务文档。替身通过不证明真实服务可用。
- macOS 是本轮实测平台；Linux、Windows、其他机器以及其他 Python 版本未验证。
  Windows 的 `fcntl` 适配不在 V0.1 承诺内。
- 引用校验能证明来源范围，不能证明模型语义；无日期资料与旧索引不能等同实时事实。
  mtime/size 检查存在同尺寸、同秒级改写盲点，需 `sync` 校验原始字节。
- 本地操作和审计收尾不是同一事务；收尾失败可能已有操作落库，重试前需检查状态。
  模型用量仅统计收到的有效响应；`--purge` 保留脱敏审计。
- V0.1 的文件、检索、上下文、轮次与超时上限，以及未提供的写入/自动化/多轮能力，
  见 [README 限制清单](../README.md#v01-限制与交付边界)。不提供原始 traceback 的 `--debug` 模式。
- 负责人已授权将 T06 与既有本地阶段成果推送至 `origin/main`；未合并分支或 Worktree、
  修改其他业务项目或进入后续版本。

## 六项交接摘要

- **完成项**：T05 按授权提交；T06 离线回归、两套独立新环境安装与演示、退出码排查和限制清单完成并通过负责人验收。
- **文件**：`README.md`、`docs/STATUS.md`、本报告、`tests/test_v01_acceptance.py`。
- **测试结果**：三套环境各 463 项通过，UC01–UC10 均通过；ruff、格式及 diff 检查通过。
- **风险**：真实模型与其他平台未验证；引用语义、索引新鲜度和审计事务边界按上文处理。
- **未做事项**：未合并分支或 Worktree、未调用真实模型或触及业务资料；产品代码与依赖未改。
- **下一任务**：后续工作需由负责人另行明确指派。
