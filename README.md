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
```

输出一行 JSON；退出码 `0` 成功、`1` 失败、`2` 登录态失效（重新登录即可）。命令幂等，每天自动跑就建一条每 2 小时执行 `all` 的自动化。

## 运行环境

依赖本机桌面端登录态解密凭据，云端沙箱不可用（返回 `NO_AUTH_FILE`）；手机 App「连接电脑」模式可用。

## 许可

MIT。凭据解密改编自 `88lin/workbuddy-auto-signin`（MIT），原始声明见 `LICENSE`。
