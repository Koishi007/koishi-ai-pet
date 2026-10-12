"""动作队列生命周期信号测试：action_finished 只在动作真正结束时发出，打断不发。"""

from PySide6.QtCore import QObject, Signal

from pet.action.action_queue import ActionQueue


class _FakeGravity:
    falling = False
    suppress_idle = False
    fall_on_tick = False  # 模拟 tick 时恰好失足

    def _tick(self):
        if self.fall_on_tick:
            self.falling = True


class _FakeAnim(QObject):
    animation_finished = Signal(str)
    is_playing = True  # 动作能播起来 → 走正常结束路径

    def stop(self):
        pass


class _FakeActions(QObject):
    walk_finished = Signal()

    def __init__(self):
        super().__init__()
        self.gravity = _FakeGravity()
        self._anim = _FakeAnim()
        self.fail_actions: set[str] = set()
        self.ran: list[str] = []

    def sit(self, *args, **kwargs):
        if "sit" in self.fail_actions:
            raise RuntimeError("动作本体炸了")
        self.ran.append("sit")

    def idle(self):
        pass

    def _stop_drive(self, switch_idle=True):
        pass

    def stop_all_anims(self):
        pass


def _make_queue():
    actions = _FakeActions()
    queue = ActionQueue(actions)
    finished: list[str] = []
    queue.action_finished.connect(finished.append)
    return actions, queue, finished


class TestActionFinished:
    def test_not_emitted_while_running(self):
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        assert finished == []  # 动作还在跑，不能提前结算

    def test_emitted_after_animation_ends(self):
        actions, queue, finished = _make_queue()
        queue.enqueue("sit")
        actions._anim.animation_finished.emit("sit")
        assert finished == ["sit"]

    def test_emitted_on_timeout(self):
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue._on_action_timeout()
        assert finished == ["sit"]

    def test_emitted_once_per_action(self):
        actions, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue.enqueue("sit")
        actions._anim.animation_finished.emit("sit")
        assert finished == ["sit"]
        actions._anim.animation_finished.emit("sit")
        assert finished == ["sit", "sit"]

    def test_emitted_when_ending_exactly_while_falling(self):
        # 播完的瞬间恰好失足：pause 会清掉记录，这里必须先结算，否则产出永久丢失
        actions, queue, finished = _make_queue()
        queue.enqueue("sit")
        actions.gravity.fall_on_tick = True
        actions._anim.animation_finished.emit("sit")
        assert finished == ["sit"]

    def test_not_emitted_when_action_body_fails(self):
        actions, queue, finished = _make_queue()
        actions.fail_actions.add("sit")
        queue.enqueue("sit")
        assert finished == []  # 没跑起来，不算完成

    def test_not_emitted_when_cleared(self):
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue.clear()
        assert finished == []

    def test_not_emitted_when_stopped(self):
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue.stop()
        assert finished == []

    def test_not_emitted_on_pause(self):
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue.pause()
        assert finished == []

    def test_not_emitted_after_resume(self):
        # resume 从下一项继续，被挂起的动作不会重跑，也不结算
        _, queue, finished = _make_queue()
        queue.enqueue("sit")
        queue.pause()
        queue.resume()
        assert finished == []


class TestClearLifecycle:
    """clear 后队列必须重新可用；显式停止不被外部恢复越过。"""

    def test_enqueue_after_clear_runs(self):
        actions, queue, _ = _make_queue()
        queue.pause()
        queue.clear()
        queue.enqueue("sit")
        assert actions.ran == ["sit"]

    def test_hold_requires_pause_after_clear(self):
        # 抓取流程：clear 之后再 pause，按住期间的新动作只入队不执行
        actions, queue, _ = _make_queue()
        queue.clear()
        queue.pause()
        queue.enqueue("sit")
        assert actions.ran == []

    def test_resume_ignores_stopped_queue(self):
        actions, queue, _ = _make_queue()
        queue.stop()
        queue.enqueue("sit")
        queue.resume()  # 落地/松手等外部恢复不得越过停止
        assert actions.ran == []
        queue.start()
        assert actions.ran == ["sit"]

    def test_start_clears_pause_left_by_stop(self):
        # 暂停态残留会让 start 之后的队列永不推进
        actions, queue, _ = _make_queue()
        queue.pause()
        queue.stop()
        queue.enqueue("sit")
        queue.start()
        assert actions.ran == ["sit"]
