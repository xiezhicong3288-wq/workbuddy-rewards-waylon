# workbuddy-rewards

自动签到、旅行与任务中心领积分。

## 安装

复制到 `~/.workbuddy/skills/workbuddy-rewards/`。需要 Python 3.10+（仅标准库）与已登录过的 WorkBuddy 桌面客户端。

## 用法

```bash
python scripts/main.py doctor     # 排障，离线
python scripts/main.py status     # 查状态，只读
python scripts/main.py checkin    # 签到，100 分
python scripts/main.py travel     # 领奖或派遣，5–10 分
python scripts/main.py tasks      # 看任务中心还有哪些任务，只读
python scripts/main.py tasks --claim   # 接上未开启的任务并领走已完成的奖励
python scripts/main.py all        # 签到 + 旅行 + 任务领取
python scripts/main.py schedule --from 09:30 --to 18:30   # 按开机时段推荐排班
```

输出一行 JSON（`schedule` 除外，它输出人读的建议）；退出码 `0` 成功、`1` 失败、`2` 登录态失效（重新登录即可）。命令幂等，重复运行不会重复领取。

任务只有被「接」下之后才开始计数，且只有 `completed` 状态能领奖，所以建议先跑一次 `tasks --claim`，
再去完成任务，回头再跑一次收积分。需要真实操作的任务会列在 `waiting` 里并附做法提示。

这个目录本身不带任何定时任务，不配自动化就一次都不会自动跑。定时只在电脑醒着时才有意义，
而一趟旅行要 1–4 小时、派遣必须赶在关机前被领掉，所以频率要按自己的开机时段算：
`python scripts/main.py schedule --from <开机时间> --to <关机时间>` 会给出适合的 `rrule` 与命令，
再套用 [references/automation-prompt.md](references/automation-prompt.md) 里的模板即可。

## 运行环境

依赖本机桌面端登录态解密凭据，云端沙箱不可用（返回 `NO_AUTH_FILE`）；手机 App「连接电脑」模式可用。

## 许可

MIT。凭据解密改编自 `88lin/workbuddy-auto-signin`（MIT），原始声明见 `LICENSE`。
