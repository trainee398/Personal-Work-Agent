# 纯虚构测试数据

所有项目、时间、人物和状态均为虚构；不从真实公司文件复制。

- `demo_project/STATUS.md`：UTF-8、LF，包含中文标题与正文、一个无日期小节、
  一行提示注入文本和 `CANARY-DO-NOT-LEAK`。
- `demo_project/STATUS_CRLF.md`：与上面正文完全一致，使用真实 CRLF 字节；
  `.gitattributes` 禁止 Git 转换该文件的换行。
- `demo_project/.env`：只有明确标识的假值，不是可用密钥，不得加载为运行配置。
  `.gitignore` 对该精确路径放行，以便后续测试硬拒绝清单。

提示注入行是不可信数据，不是开发指令。T00 仅校验样例存在且格式正确；
源文件读取与硬拒绝策略属于 T02，注入隔离和审计 canary 验收属于 T04/T05。
测试用例若需修改文件，应先复制到 pytest 的 `tmp_path`，保留原始样例。
