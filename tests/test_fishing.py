"""钓鱼判定测试：命中概率、稀有度分档、鱼种表与文案。"""

import pytest

from pet.action import fishing


class TestRollCatch:
    def test_miss_when_random_above_rate(self, monkeypatch):
        monkeypatch.setattr(fishing.random, "random", lambda: 0.99)
        assert fishing.roll_catch() is None

    def test_boundary_value_counts_as_miss(self, monkeypatch):
        # 判定用 >=：随机值正好等于 CATCH_RATE 时算没钓到
        monkeypatch.setattr(fishing.random, "random", lambda: fishing.CATCH_RATE)
        assert fishing.roll_catch() is None

    def test_hit_below_rate_returns_fish_and_rarity(self, monkeypatch):
        monkeypatch.setattr(fishing.random, "random", lambda: fishing.CATCH_RATE - 0.01)
        monkeypatch.setattr(fishing.random, "choice", lambda seq: seq[0])
        result = fishing.roll_catch()
        assert result is not None
        name, rarity = result
        assert name in fishing.FISH_TABLE[rarity]

    def test_rarity_follows_weighted_choice(self, monkeypatch):
        monkeypatch.setattr(fishing.random, "random", lambda: 0.0)
        monkeypatch.setattr(fishing.random, "choices", lambda seq, weights, k: ["legendary"])
        monkeypatch.setattr(fishing.random, "choice", lambda seq: seq[0])
        assert fishing.roll_catch() == (fishing.FISH_TABLE["legendary"][0], "legendary")


class TestFishTable:
    def test_thirty_species(self):
        assert sum(len(group) for group in fishing.FISH_TABLE.values()) == 30

    def test_no_duplicate_names(self):
        names = [n for group in fishing.FISH_TABLE.values() for n in group]
        assert len(names) == len(set(names))

    def test_every_rarity_has_weight(self):
        assert set(fishing.FISH_TABLE) == set(fishing._RARITY_WEIGHTS)


class TestFormatResult:
    def test_miss_line(self):
        line = fishing.format_result(None)
        assert line
        assert not any(name in line for group in fishing.FISH_TABLE.values() for name in group)

    @pytest.mark.parametrize("rarity", list(fishing.FISH_TABLE))
    def test_result_includes_fish_name(self, rarity):
        name = fishing.FISH_TABLE[rarity][0]
        line = fishing.format_result((name, rarity))
        assert line
        assert name in line

    @pytest.mark.parametrize("rarity", list(fishing.FISH_TABLE))
    def test_wording_follows_rarity_table(self, rarity):
        name = fishing.FISH_TABLE[rarity][0]
        prefix, suffix = fishing._RARITY_WORDING[rarity]
        line = fishing.format_result((name, rarity))
        assert line.endswith(suffix)
        if prefix:
            assert prefix in line

    def test_rarities_render_distinctly(self):
        lines = {rarity: fishing.format_result((fishing.FISH_TABLE[rarity][0], rarity))
                 for rarity in fishing.FISH_TABLE}
        assert len(set(lines.values())) == len(lines)

    def test_common_has_no_exclamation(self):
        # 占七成的寻常收获要压平语气，不能和上游档同样兴奋
        assert "！" not in fishing.format_result((fishing.FISH_TABLE["common"][0], "common"))
