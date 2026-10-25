# 0012 登录失效由最近一次请求标记

- 状态：已采纳
- 相关代码：`pet/tools/bilibili/core.py`（`mark_credential_invalid`、`credential_invalid`、`BiliClient._raise_api`）、`pet/tools/bilibili/panel.py`（`refresh_login_state`、`_on_qr_clicked`、`show_panel`）
- 相关文档：[specs/2026-10-21-bili-video-browse-design.md](../specs/2026-10-21-bili-video-browse-design.md)

## 背景

- 面板只判断凭证文件是否存在，文件在就显示「已登录」。sessdata 过期后接口返回 `-101`，文件仍在，面板与接口状态相反。
- 面板窗口关闭后对象仍存活，`show_panel` 复用实例直接 `show()`，登录态停在此前读到的快照。
- 失效状态下 `_on_qr_clicked` 仍以「有凭证」为由拒绝扫码，用户必须先点「退出登录」。

## 决定

- `BiliClient` 在接口返回 `-101`/`-111` 时写 `bilibili_credential.invalid` 标记，与凭证同目录。
- `credential_invalid()` 只在标记比凭证文件新时返回真；重新扫码保存凭证即恢复已登录，标记不成为长期状态。
- 面板每次打开都重读凭证：无凭证显示「未登录」，凭证存在但标记生效显示「登录已失效，需要重新扫码」，该状态下允许直接扫码，不必先退出。
- 标记只由请求触发：长时间不发请求时面板仍显示「已登录」，直到下一次接口报 `-101`。

## 后果

- 面板状态与最近一次接口结果一致，打开面板不发探测请求。
- `-412` 风控与网络错误不写标记，避免把临时故障当成登录失效。
- 标记文件只存时间戳，不含登录态；退出登录时一并删除。
