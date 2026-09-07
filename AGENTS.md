# Sandglass · 给接手的 agent

Sandglass 是公众版、只读的本地 AI 用量仪表盘。它保留浏览器面板、Windows 悬浮球和托盘壳，当前读取 Claude、Codex、Grok 的本机记录与账号额度。

## 最高边界：厂商目录只读

任何正常运行路径都不得创建、修改、移动或删除 `~/.claude`、`~/.codex`、`~/.grok` 或 Grok 桌面 app 账号目录中的文件。

- 不刷新 OAuth/OIDC 凭据；过期就报告需要回官方客户端登录。
- 不提供账号切换 API、按钮或隐式切换。
- 不读取 Grok Worker、`codex-auth-web` 或其他 GOGO 产品的数据。
- Sandglass 只能写自己的 `SANDGLASS_HOME`，默认 `%LOCALAPPDATA%\sandglass`。
- 桌面版只有用户主动勾选“开机启动”时才能写当前用户的 Run 注册表项。

如果新增厂商适配器，先写一个运行级测试，证明执行账号发现、日志采集和额度读取后，厂商目录逐字节未变化。

## 先找直接信源

优先级固定：官方客户端逐轮日志 > 官方账号注册表/认证事件 > 厂商第一方额度接口 > Sandglass 自己的观测台账。不得用额度百分比反推逐轮消耗，不得用面板派生值审计面板自身。

第一方不等于稳定公开 API。信源缺失或接口失效时，保留能够直接证明的部分并明确降级，不猜测、不补偿。

## 三个口径

| 名称 | 定义 |
| --- | --- |
| 消耗 | `total_tokens`，实际进入额度计量的本机量 |
| 产出 | `output_tokens`，已经包含 reasoning |
| 额度条 | 厂商账号级 `used_percent`，可能包含其他设备 |

不得把 reasoning 再加进 output，也不得把产出当成额度消耗。

## 账号归属

- 新用户必须先选择使用形态。用户选择“单账号、只使用官方工具”后，每个厂商的
  官方本机 Token 都归到该厂商当前登录账号；扫描到旧账号或第二个账号不得擅自改选。
  该归属必须标记 `sandglass_policy:single_official_account`。
- 单账号策略只在读取时合并，绝不写进会话缓存或身份账本。切回多账号/多工具模式后，
  必须立刻恢复直接证据归属与未归属，所有原始账本文件逐字节不变。
- 模式选择界面必须允许返回而不保存；进入设置不能等同于改变模式。
- 多账号、多工具，或任何需要重建、补齐、更新账本的情况进入
  `skills/sandglass-adapter/SKILL.md`。已有官方身份事件或用户适配器直接证明的本机
  Token 仍正常归属；只有缺少关系的部分保持未归属，不能因进入 skill 模式而抹掉证据。
- Codex：开启后的官方 OTel 日志可直接携带账号与逐请求 Token，是首选信源。公开版不得读取或依赖第三方 `~/.codex/accounts/registry.json`；当前官方 `auth.json` 只能证明观测时的登录账号，Sandglass 从观测时刻起在自己的目录记录身份变化。
- Grok：CLI `auth init user_info check` 是覆盖期内的直接信源；更早历史可由官方桌面 app 日志补齐。
- 多账号/多工具模式中，无法证明的旧记录保持未归属，不得借当前登录身份重写历史。
- 归属按分钟计算；跨切号会话不能整场判给一个账号。

三家完整信源等级、覆盖范围与降级方式见 `docs/provider-source-map.md`。实现新的归属路径前必须先更新该图，并用另一种方法验证测量仪器本身。

## 解析事实

- Claude resume 会复制父会话历史；只在文件内按出生时间和回放形状裁掉复制段。
- Codex fork 会在开头重放父历史；由 `_codex_replay_prefix` 处理。
- Claude `iterations` 不含完整思考明细；顶层 usage 才是 reasoning 权威值。
- Grok `turn_completed` 是每轮增量，不是累计值。
- 修改 collector 必须提升 `RECORD_FORMAT`。

## 运行与验证

### 唯一用户可见运行源

每台维护机器上，用户正在验收的 Sandglass 只能从该机器明确登记的正式
checkout 启动。Agent 的临时 worktree 不得占用产品默认端口，也不得替换
正在跑的桌面进程。

- 接管、重启或声称界面已更新前，必须分别核对正式 checkout 的当前分支、HEAD、
  工作区状态，以及默认端口上的实际进程；不能只看当前 shell 的 cwd。
- 如果改动产生在临时工作树，必须先合并或等价移植到正式 checkout，从主线
  重新运行测试，再从那里重启用户可见进程。
- 验收网页必须读取主线当前的静态资源版本；看到旧版 `i18n.js?v=`、旧语言菜单或
  旧布局时，先查运行源，不得把版本回退误判成浏览器缓存。
- 改动被主线吸收后，删除对应旧分支并注销临时 worktree。未合并或工作区不干净时
  不得强删，先审计差异。

```powershell
python -m unittest discover -s tests
python -m sandglass serve --no-browser
$env:PYTHONUTF8='1'; python -m tools.audit
```

Python 业务代码修改后必须重启服务；只改 `index.html` 可刷新页面。默认面板端口 7740；桌面单实例使用当前登录会话内的 Windows 命名互斥体，不占额外端口。

## 模型路由入口

模型选择、Sol 升级边界与 worker 返回字段见 [`docs/model-routing.md`](docs/model-routing.md)。这是既有源、只读、安全、发布和实验治理的附加入口，不覆盖或改写本文件原条款；Luna 不得最终验收自己的成果。

Claude Code 读 `CLAUDE.md`（内含 `@AGENTS.md`，即本文件）；角色路由见 `CLAUDE.md` 和 `.claude/agents/`。同样是附加入口，不覆盖本文件任何原条款。
