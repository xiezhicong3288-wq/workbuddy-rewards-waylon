# workbuddy-rewards

自动领取 WorkBuddy 签到与旅行积分。

## 安装

复制到 `~/.workbuddy/skills/workbuddy-rewards/`。需要 Python 3.10+（仅标准库）与已登录过的 WorkBuddy 桌面客户端。

## 用法

```bash
python scripts/main.py doctor     # 排障，离线
python scripts/main.py status     # 查状态，只读
python scripts/main.py checkin    # 签到，100 分
python scripts/main.py travel     # 领奖或派遣，5–10 分
python scripts/main.py all        # 签到 + 旅行
python scripts/main.py schedule --from 09:30 --to 18:30   # 按开机时段推荐排班
```

输出一行 JSON（`schedule` 除外，它输出人读的建议）；退出码 `0` 成功、`1` 失败、`2` 登录态失效（重新登录即可）。命令幂等，重复运行不会重复领取。

## 新机器上手三步

1. **装好并自检**：复制目录 → `python scripts/main.py doctor`，看到 `"status":"ready"` 即可。
2. **先手动跑一次**：`python scripts/main.py all`，确认能签到、能派遣。
3. **配自动化**：`python scripts/main.py schedule --from <开机时间> --to <关机时间>`
   会算出适合你的 `rrule` 和命令，然后把 [references/automation-prompt.md](references/automation-prompt.md)
   里的模板填好贴进你的定时任务。

**这个仓库本身不带任何定时任务**，下载后不配自动化就一次都不会自动跑。之所以不写死一个频率，
是因为定时只在电脑醒着时才有意义，而且一趟旅行要 1–4 小时，派遣必须赶在关机前被领掉——
所以频率得按你自己的开机时段算。

## 运行环境

依赖本机桌面端登录态解密凭据，云端沙箱不可用（返回 `NO_AUTH_FILE`）；手机 App「连接电脑」模式可用。

## 许可

MIT。凭据解密改编自 `88lin/workbuddy-auto-signin`（MIT），原始声明见 `LICENSE`。
