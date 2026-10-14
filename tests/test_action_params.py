"""动作数值参数测试：模型给的值转不成 int 时回落到默认值。"""

from pet.action.action import coerce_int


def test_numbers_pass_through():
    assert coerce_int(60, 400) == 60
    assert coerce_int("60", 400) == 60


def test_non_numeric_falls_back():
    assert coerce_int("|", 25) == 25
    assert coerce_int("100px", 400) == 400
    assert coerce_int(None, 25) == 25
