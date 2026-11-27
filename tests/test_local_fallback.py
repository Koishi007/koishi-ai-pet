"""本地兜底产出：情绪走协议白名单，动作是注册表里的真实动作。"""

from pet.action.registry import ACTION_NAMES
from pet.brain.local_fallback import chat_decide_local, decide_local, interact_decide_local
from pet.brain.output import EMOTION_NAMES


def test_all_local_outputs_use_whitelisted_emotions():
    outputs = [decide_local(), chat_decide_local("在吗")]
    outputs += [interact_decide_local(hint) for hint in
                ("投喂了苹果。", "抓起", "放下", "摸摸头")]
    assert all(o.emotion is None or o.emotion in EMOTION_NAMES for o in outputs)
    assert all({a.name for a in o.actions} <= set(ACTION_NAMES) for o in outputs)
