# 0007 动作超时算正常结束并结算产出

- 状态：已采纳
- 相关代码：`pet/action/action_queue.py`（`_on_action_timeout`、`_settle_current`、`_on_action_done`）、
  `pet/ui/pet_window.py`（`_on_action_finished`）、`pet/action/outcome.py`
- 相关文档：[subsystems/actions-and-animation.md](../subsystems/actions-and-animation.md) §1

## 背景

动作可能因为素材异常、信号丢失或平台差异而永远不进入结束态。产出（outcome）与事件上报都挂在
「动作正常结束」上 - 如果超时被当成「没发生」，钓鱼这类玩法会静默丢结果，用户会觉得「明明动了却没有反应」。

## 决定

- **超时走与正常结束相同的路径**（`_settle_current` → 发 `action_finished` → 结算产出 → 播特效）；
- 但 `clear()` / `stop()` / `pause()`（拖拽打断、切换场景）**不算完成**，不发 `action_finished`；
- 特例：动作播完的瞬间恰好开始下落时，**先结算再挂起队列**，否则产出会随 pause 丢失；
- 另一条路：`outcome.register(..., timing="start")` 的产出挂在 `action_started` 上，
  **不经过结束信号**（如 `calling` 的来电通知），因此既不受超时兜底影响，被打断时也已经发过了。

## 后果

- 玩法结果不会被一次卡住丢失；玩家视角「动作播了就有结算」；
- 代价：极端情况下一次动作可能被结算两次或被误结算（例如其实没播成功），
  取舍是「宁可能量条多扣一次，也不丢玩法产出」；
- 有测试锁定这三条路径（`tests/test_action_queue_events.py`）。
