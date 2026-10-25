# 0011 评论发出后立即复核可见性

- 状态：已采纳
- 相关代码：`pet/tools/bilibili/duty.py`（`_confirm_comment`、`_reply_visibility`、`_guarded_write`、`_as_tool_result`、`verify`）
- 相关文档：[specs/2026-10-21-bili-video-browse-design.md](../specs/2026-10-21-bili-video-browse-design.md) §7

## 背景

- B 站评论接口返回 `code=0` 与 `rpid` 只表示写入被受理。被阿瓦隆折叠的评论同样返回 `rpid`，列表里 `state=17`，只有自己能看到。
- 台账把「拿到回执」记为 `done`，而可见性只由 `bilibili__verify` 收敛。该方法是手动工具，依赖模型主动调用；线上两条回复停在 `done`，实际不可见。
- `done` 被读成「已公开」，用户看到的结果就是「发了但看不见」。

## 决定

- 评论发出后记 `done`，工具立即返回；60 秒后由后台线程用同一份凭证拉一次列表（顶层用 `reply/main`，楼中楼用 `reply/reply`），按 `rpid` 找本人那条并读 `state` 与 `invisible`：
  - `state=17` 或 `invisible` → `hidden`；
  - `state=0` → `visible`；
  - 其他非 0 状态或列表里还没出现 → 维持 `done`，交给 `verify` 复核。
- 延时原因是列表刷新有延迟，发出瞬间查不到会误记 `done`。评论工具本身不等待，超时预算不变。
- 复核出错只记日志，已发出的评论仍算 `done`。后台线程用 daemon，进程退出时未执行的复核直接丢弃，由后续复核兜底。
- `hidden` 计入有效记录数，同一视频不会因为被折叠而反复补发；被折叠的回复仍标记对应 @ 已处理，避免对同一条 @ 反复重试。

## 后果

- 每条评论在发出 60 秒后多一次列表请求。
- 进程在 60 秒内退出时不做复核，记录停在 `done`，需要 `verify` 收敛。
- 发布后一分钟内列表仍未更新时仍是 `done`；`done` 的语义是「已受理、可见性未确认」。
- 被折叠的评论在复核后变 `hidden`，但没有主动通知主人的通道，需要后续交互或 `verify` 才能看到这个结果。
- 折叠由平台判定，代码只能如实上报，无法让被折叠的评论变为公开。
