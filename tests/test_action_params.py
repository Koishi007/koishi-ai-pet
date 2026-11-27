"""动作数值参数测试：模型给的值转不成 int 时回落到默认值。"""

from pet.action.action import coerce_int


def test_numbers_pass_through():
    assert coerce_int(60, 400) == 60
    assert coerce_int("60", 400) == 60


def test_non_numeric_falls_back():
    assert coerce_int("|", 25) == 25
    assert coerce_int("100px", 400) == 400
    assert coerce_int(None, 25) == 25


def test_registry_actions_have_controller_method():
    """注册表动作必须在 PetActions 上有同名方法，否则队列取不到会静默跳过。"""
    from pet.action.action import PetActions
    from pet.action.registry import ACTION_NAMES

    missing = [name for name in ACTION_NAMES
               if not callable(getattr(PetActions, name, None))]
    assert missing == []
