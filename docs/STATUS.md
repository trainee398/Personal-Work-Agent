# WorkPilot 任务状态

当前：T02、T03 已接受；负责人授权 T03 本地提交，随后只实施 T04，不推送。唯一规格：[V0_1_SPEC.md](V0_1_SPEC.md)。
开始核查：`main` 工作区干净，HEAD 为 `1554524`，已含部分 T03 模型/仓储/读取快照能力；
本地跟踪引用 `origin/main` 同为该提交，本轮未联网核验远端，也未重复创建 T02 提交。

规格原样保留：36042 字节，SHA-256
`cc2acbc2d643d8028756baea25efc0f7289c8173d98c46dee04ee16387361ffb`。

## 规格疑问

- **负责人已裁决（2026-10-30）**：tracing 强制设为 `1`，T04 引入 SDK 时固定已验证版本；
  保留 `projects.allowed_files_json` 和 `projects.active`。普通移除删 chunks、停用来源，保留记忆/名称；
  确认清除后删除登记/来源/记忆，审计保留并解除关联。仍为七张表，不用未索引来源伪造哈希/时间。
- **负责人已裁决（2026-10-30）**：doctor 只检查、不建库/迁移/修复；未初始化返回 6，
  FTS5 缺失与锁占用仅提示。此前完整裁决见下方历史快照。
- **T02 实现已接受**：长段首块含实际标题行，续块保留面包屑及真实连续行号；单行超过 2000 字符拒绝（4）。
  缺失/撤权/安全校验失败停用来源并删 chunks；事务失败保留旧哈希/切片；读取期间变化清理旧切片后返回 7。
- **T03 实现已接受：同步时间口径**：schema 无项目级同步尝试时间，status 明示取已记录来源最大 `indexed_at`；
  无记录不造时间。未索引文件用 `unindexed`/空元数据，当前缺失用 `missing`，停用索引用 `inactive`。
  缺失/未索引/过期属于正常报告告警（0）；项目不存在 3，策略拒绝 4，配置错误 6，检查期间变化 7。
- **T03 实现已接受：摘录口径**：四项固定报告之后展示每个可用来源首块的最多 400 字符及真实引用，
  注明“在上次同步时读取到”；缺失、失效、撤权或检测到 stat 变化时隐藏旧正文，不在 status 内修改索引。
  仅过期的缓存保留摘录并告警。mtime/size 检查无法发现同尺寸、同秒级 mtime 的改写，需 sync 校验字节哈希。

## 交接记录

历史完整六项交接保留在提交快照：
[T00 · 1369946](https://github.com/trainee398/Personal-Work-Agent/blob/1369946422bf3830f29d5874c73c01636bd54718/docs/STATUS.md)；
[T01 · 9cee427](https://github.com/trainee398/Personal-Work-Agent/blob/9cee427/docs/STATUS.md)；
[T02 与既有裁决 · 1554524](https://github.com/trainee398/Personal-Work-Agent/blob/1554524/docs/STATUS.md)。
T01 实现为 `f52c759`；T02 已接受并提交 `e897e6184af3b7840847526e9ce0afa15da3cf34`。
本轮续作前复查现有测试 **229 passed in 1.66s**；已有 T03 仓储导入/格式问题已在本轮修正。

### 2026-10-30 · T03 离线状态与人工记忆（负责人已接受，授权本地提交）

提交前复查：pytest **296 passed in 2.37s**；ruff check、ruff format --check、git diff --check 均通过。

- **完成项**：status 固定顺序输出来源/行号/时间/告警与有引用的缓存摘录，支持可配置过期提示；
  元数据检查沿用 T02 安全文件描述符，不读源正文。memory add/list 支持三种人工类别、筛选、
  UTC 时间、跨进程持久化和稳定排序；明确区分人工记忆与文件证据，空白输入拒绝，终端控制字符可见转义。
- **文件**：本轮 11 个文件。新增 `src/workpilot/{status,memory}.py`、`tests/test_{status,memory}.py`；
  更新 CLI、security/policy、storage/repositories、test_cli/test_sync、README 和本页。
  接续已有 `1554524`；schema、依赖、规格、AGENTS 和原始 fixture 未改。
- **测试结果（已执行，macOS / Python 3.12.14）**：`.venv/bin/python -m pytest -q`：
  **296 passed in 2.49s**（新增 67 项）；`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`
  （41 files）、`git diff --check` 均通过。已安装 CLI 在临时虚构项目完成 **17 步演示**，
  无 API Key 且拦截 DNS/TCP；覆盖未索引/同步/重复报告/过期/缺失清理/恢复/人工记忆/doctor，
  输出重复一致，源文件哈希前后一致，schema 1、目录/数据库 0700/0600，审计表仍为空。
- **风险**：同步时间与摘录口径已随阶段成果接受，规格正文仍由负责人统一；状态是缓存快照而非实时进度。
  只验证 macOS / Python 3.12.14；Python 3.11、Linux/Windows 和独立新环境安装未执行。
- **未做事项**：本轮未新建提交、未推送/合并、未实施 T04–T06；未读取真实公司文件/密钥，
  未运行外部业务操作或真实付费模型；SDK、Agent、ask/dry-run、审计写入留在后续任务。
- **下一任务**：负责人已接受 T03；完成本地提交后，只实施 T04。
