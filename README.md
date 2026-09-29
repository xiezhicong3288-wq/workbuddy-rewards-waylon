# workbuddy-rewards

自动领取 WorkBuddy 每日积分：加油站签到（100 分）＋ Buddy 旅行（5–10 分，每天一趟）。

## 安装

复制到 `~/.workbuddy/skills/workbuddy-rewards/` 即可，无第三方依赖。

环境要求：Python 3.10+（仅标准库），且本机 WorkBuddy 桌面客户端**登录过一次**——登录态用于本地解密凭据。

## 用法

```bash
python scripts/main.py doctor     # 排障，离线不解密
python scripts/main.py status     # 查状态，只读
python scripts/main.py checkin    # 加油站签到
python scripts/main.py travel     # 领奖或派遣（一次一跳）
python scripts/main.py all        # 签到 + 旅行
```

输出恒为单行 JSON。退出码：`0` 成功或正常空操作、`1` 失败、`2` 登录态失效（打开客户端重新登录即可，**不要把令牌贴到对话里**）。

可选参数：`--location`、`--loop`、`--max-hours`、`--poll-seconds`、`--no-log`。

要每天全自动，在 WorkBuddy 里建一条周期性自动化，每 2 小时跑 `.../scripts/main.py all`。命令幂等，多跑不会重复领取。

## 运行环境

依赖**本机桌面端**的登录态解密凭据，因此云端沙箱跑不通（返回 `NO_AUTH_FILE`）；手机 App 用「连接电脑」模式可用。

## 许可

MIT。凭据解密部分改编自 `88lin/workbuddy-auto-signin`（MIT），原始声明保留在 `LICENSE` 与 `scripts/credentials.py`。
