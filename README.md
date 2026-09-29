# workbuddy-rewards

WorkBuddy 每日积分助手：一个 Skill，覆盖 WorkBuddy **全部两个**积分渠道，每天自动领取，无需人工介入。

- **Buddy 加油站签到** — 每天 100 积分（加油站就是签到本身，连签/周累计奖励由服务端自动发放）
- **Buddy 旅行** — 派遣猫猫，到站后领取 5–10 积分，自动接续下一趟

## 快速开始

```bash
# 查状态（只读，不改动账号）
python scripts/main.py status

# 排障（离线，不解密令牌）
python scripts/main.py doctor

# 完成当天全部积分任务（签到 + 旅行）
python scripts/main.py all

# 最大化：领奖后立刻再派遣，直到当日额度用完
python scripts/main.py all --loop --max-hours 8
```

Windows 若没有全局 Python，用自带的包装器（注意必须绕开执行策略）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 status
```

## 安装

把整个目录复制到 skills 目录即可，无需依赖安装：

| 位置 | 路径 |
| --- | --- |
| 用户级 | `~/.workbuddy/skills/workbuddy-rewards/` |
| 项目级 | `<project>/.workbuddy/skills/workbuddy-rewards/` |

环境要求：

- Python **3.10+**（仅标准库，无第三方依赖）
- 已安装并**登录过一次**的 WorkBuddy 桌面客户端（登录态用于本地解密）

## 命令与退出码

| 命令 | 作用 | 是否写操作 |
| --- | --- | --- |
| `doctor` | 检查登录态与解密运行时 | 否 |
| `status` | 查看签到与旅行状态 | 否 |
| `checkin` | 加油站签到 | 是 |
| `travel` | 领奖或派遣（一次一跳） | 是 |
| `all` | 签到 + 旅行 | 是 |

可选参数：`--location <id|code>`、`--loop`、`--max-hours <h>`、`--poll-seconds <s>`、`--no-log`。

退出码：`0` 成功或正常空操作；`1` 网络/协议/业务失败；`2` 本地登录态失效（打开客户端重新登录即可，**不要把令牌贴到对话里**）。

输出恒为单行 JSON，便于脚本解析：

```json
{"checkin":{"status":"already_checked","credit":100,"streak_days":4,"total_credits":400},
 "travel":{"status":"traveling","location":"咖啡馆","reward_credit":7,"remaining_seconds":9392}}
```

## 每天全自动

在 WorkBuddy 里创建一条周期性自动化，让它每 2 小时运行一次：

```bash
python "$HOME/.workbuddy/skills/workbuddy-rewards/scripts/main.py" all
```

建议的提示词约束：空操作完全静默，仅在「签到成功 / Buddy 出发 / 领到奖励 / 出错」时通知。详见 [references/scheduling.md](references/scheduling.md)。

因为签到每天只需一次、旅行需数小时一轮，2 小时间隔足以让一条自动化覆盖全天；命令本身幂等，重复运行不会重复领取。

## 关于「平台奖励」

WorkBuddy 有两类完全不同的货币，**只有前者才是积分**：

- **积分 credits** — 签到与旅行产出，本 Skill 负责最大化。
- **资源额度 quota** — 套餐自带的模型额度，由套餐直接授予，**不存在领取动作**。

已核查并排除的干扰项：`/v2/activity/growth/buddy/info` 仅返回宠物外观；`/v2/activity/banner` 返回活动已下线；邀请/推广大使类接口属拉新，永远不碰。四个旅行地点收益完全相同（5–10 分 / 1–4 小时），所以选哪个地点都没有优化空间，收益只取决于「到站到再次出发」的空档——这正是 `--loop` 优化的地方。

## 安全设计

- 只读取**当前系统用户**自己的 WorkBuddy 登录态，令牌不出本进程、不落盘、不进入日志
- 网络请求在代码层锁定为已知 WorkBuddy/Tencent 域名与已核实路径，不猜测、不探测陌生接口
- 加密登录态交给本机 WorkBuddy 客户端自身的运行时解密（stdin 传入密文，内存管道回传明文）
- 日志只记录允许字段的结果摘要，不含请求头、原始响应、账号标识或可执行文件路径
- 每次调用最多一次状态转移；写操作结果不明确时直接终止，绝不盲目重试
- 拒绝多账号 farming、凭据导入导出、规避服务端限额

## 兼容性说明

接口字段依赖服务端私有接口，WorkBuddy 随时可能调整。`--loop`、旧前缀回退、边界情况都按「失败即停」设计，接口变更时会以明确的错误码退出，而不是静默产生错误写入。参见 [references/endpoints.md](references/endpoints.md)。

## 测试

```bash
python -m unittest discover -s tests
```

27 个用例覆盖签到幂等、旅行状态机、单次写入约束、日志脱敏、令牌校验、域名白名单、运行时发现与 loop 模式。

## 许可与来源

MIT。加密凭据辅助部分改编自 `88lin/workbuddy-auto-signin`（MIT），原始版权声明保留在 `LICENSE` 与 `scripts/credentials.py` 中。协议行为还与以下社区实现交叉验证，详见 [references/sources.md](references/sources.md)。
