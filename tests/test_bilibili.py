"""B 站工具：签名、解析、内容过滤、写操作台账。"""

from __future__ import annotations

import base64
import io
import json
from datetime import date, datetime

import httpx
import pytest
from PIL import Image

from pet.tools.bilibili import filters, schedule
from pet.tools.bilibili.core import (
    BiliClient,
    Credential,
    is_bvid,
    join_subtitle,
    mixin_key,
    parse_mentions,
    parse_search_items,
    parse_view,
    pick_subtitle,
    sign_params,
    within_days,
)
from pet.tools.bilibili import duty
from pet.tools.bilibili.storage import BiliStorage

IMG = "7cd084941338484aae1ad9425b84077c"
SUB = "4932caff0ff746eab6f01bf08b70ac45"


# ---------- 签名 ----------

def test_mixin_key_matches_reference():
    assert mixin_key(IMG, SUB) == "ea1db124af3c7062474693fa704f4ff8"


def test_sign_params_matches_reference():
    signed = sign_params({"foo": "114", "bar": "514", "zab": 1919810}, IMG, SUB, 1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    assert signed["wts"] == "1702204169"


def test_sign_params_order_independent():
    first = sign_params({"foo": "114", "bar": "514"}, IMG, SUB, 1702204169)
    second = sign_params({"bar": "514", "foo": "114"}, IMG, SUB, 1702204169)
    assert first["w_rid"] == second["w_rid"]


def test_sign_params_strips_reserved_chars():
    dirty = sign_params({"q": "a!b'c(d)e*f"}, IMG, SUB, 1)
    clean = sign_params({"q": "abcdef"}, IMG, SUB, 1)
    assert dirty["w_rid"] == clean["w_rid"]


# ---------- 解析 ----------

def test_parse_search_items_cleans_title():
    data = {"result": [{
        "bvid": "BV1", "title": '<em class="keyword">东方</em>Project',
        "author": "u", "play": "12", "pubdate": "1700000000",
        "duration": "03:00", "description": "a" * 300,
    }]}
    items = parse_search_items(data)
    assert items[0]["title"] == "东方Project"
    assert items[0]["play"] == 12
    assert len(items[0]["description"]) <= 200


def test_parse_search_items_skips_without_bvid():
    assert parse_search_items({"result": [{"title": "x"}, "bad"]}) == []


def test_parse_view_fills_defaults():
    info = parse_view({"bvid": "BV1", "owner": {"name": "up"}, "stat": {"view": 7}})
    assert info["up"] == "up"
    assert info["play"] == 7
    assert info["aid"] == 0
    assert info["pic"] == ""


def test_parse_view_absolute_cover():
    info = parse_view({"pic": "//i0.hdslb.com/bfs/archive/a.jpg"})
    assert info["pic"] == "https://i0.hdslb.com/bfs/archive/a.jpg"


def test_parse_mentions_keeps_video_only():
    data = {"items": [
        {"id": 1, "uri": "bilibili://video/123", "source_id": 456,
         "user": {"uid": 9, "nickname": "主人"}, "source_content": "@恋恋 看看这个"},
        {"id": 2, "uri": "bilibili://dynamic/456", "user": {"uid": 9}},
    ]}
    items = parse_mentions(data)
    assert len(items) == 1
    assert items[0]["aid"] == 123
    assert items[0]["uid"] == 9
    assert items[0]["rpid"] == 456
    assert items[0]["content"] == "@恋恋 看看这个"


def _cover_client(size, **kwargs):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    payload = buf.getvalue()

    def handler(request):
        return httpx.Response(200, content=payload)

    return BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                      interval=0.0, transport=httpx.MockTransport(handler), **kwargs)


def test_fetch_cover_resizes_and_encodes():
    client = _cover_client((1200, 800))
    encoded = client.fetch_cover("//i0.hdslb.com/a.png", edge=64)
    client.close()
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert max(image.size) <= 64


def test_fetch_cover_default_edge_matches_file_intake():
    client = _cover_client((2400, 1200))
    encoded = client.fetch_cover("//i0.hdslb.com/a.png")
    client.close()
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert max(image.size) == 1536


def test_pick_subtitle_prefers_ai_zh():
    subs = [{"lan": "zh-CN", "subtitle_url": "a"}, {"lan": "ai-zh", "subtitle_url": "b"}]
    assert pick_subtitle(subs)["subtitle_url"] == "b"
    assert pick_subtitle([]) is None


def test_join_subtitle_skips_blank():
    assert join_subtitle([{"content": "a"}, {"content": "  "}, {"content": "b"}]) == "a\nb"


def test_within_days_window():
    now = 1_700_000_000
    assert within_days(now - 86400, 7, now)
    assert not within_days(now - 8 * 86400, 7, now)
    assert within_days(0, 7, now)



@pytest.mark.parametrize("text,code", [
    ("看这个 https://example.com", "url"),
    ("谢谢@某人啊", "mention"),
    ("我在 D:/secret 看到的", "privacy"),
    ("作为一个AI我觉得", "identity"),
    ("这个 token 花得值", "internal"),
    ("好", "too_short"),
    ("快来关注我抽奖", "solicit"),
    ("建议停药观察", "sensitive"),
])
def test_filter_rejects(text, code):
    ok, actual = filters.check_text(text, max_chars=100)
    assert not ok
    assert actual == code


def test_filter_rejects_duplicate():
    ok, code = filters.check_text("这个视频的氛围真不错", max_chars=100,
                                  recent=("这个视频的氛围真不错",))
    assert not ok
    assert code == "duplicate"


def test_filter_rejects_custom_blocklist():
    ok, code = filters.check_text("这片子拍得不错", max_chars=100, blocklist=("片子",))
    assert not ok
    assert code == "blocklist"


def test_filter_accepts_normal_comment():
    ok, reason = filters.check_text("把无意识拍得挺有意思", max_chars=100)
    assert ok, reason


def test_filter_allows_url_only_for_dm():
    text = "看这个 https://b23.tv/abc 挺有意思"
    assert not filters.check_text(text, max_chars=200)[0]
    assert filters.check_text(text, max_chars=200, allow_urls=True)[0]


# ---------- 凭证 ----------

def test_credential_roundtrip(tmp_path):
    path = tmp_path / "credential.json"
    cred = Credential(sessdata="s", bili_jct="j", dedeuserid="9", buvid3="b",
                      ac_time_value="a")
    cred.save(path)
    assert Credential.from_file(path) == cred


def test_credential_cookie_header_matches_bilibili_api_order():
    cred = Credential(sessdata="s", bili_jct="j", dedeuserid="9",
                      buvid3="b", ac_time_value="a")
    parts = cred.cookie_header().split("; ")
    assert parts == ["SESSDATA=s", "buvid3=b", "bili_jct=j",
                     "DedeUserID=9", "ac_time_value=a"]


def test_credential_missing_fields(tmp_path):
    path = tmp_path / "credential.json"
    path.write_text('{"sessdata": "s"}', encoding="utf-8")
    assert Credential.from_file(path) is None





# ---------- 台账 ----------

@pytest.fixture
def store(case_db_path):
    return BiliStorage(db_path=str(case_db_path))


def test_claim_action_is_unique(store):
    assert store.claim_action(1, "like", "BV1") is not None
    assert store.claim_action(1, "like", "BV1") is None


def test_claim_action_allows_retry_after_failure(store):
    action = store.claim_action(1, "comment", "BV1", "x")
    store.finish_action(action, "failed", reason="risk")
    assert store.claim_action(1, "comment", "BV1", "x") is not None


def test_claim_action_scoped_by_account(store):
    assert store.claim_action(1, "like", "BV1") is not None
    assert store.claim_action(2, "like", "BV1") is not None


def test_count_today(store):
    store.claim_action(1, "like", "BV1")
    store.claim_action(1, "like", "BV2")
    assert store.count_today(1, "like", date.today().isoformat()) == 2
    assert store.count_today(1, "coin", date.today().isoformat()) == 0


def test_unseen(store):
    store.mark_seen([{"bvid": "BV1", "title": "t", "up": "u", "pubdate": 0,
                      "verdict": "picked", "note": ""}])
    assert store.unseen(["BV1", "BV2"]) == ["BV2"]


def test_save_round_accumulates(store):
    day = date.today().isoformat()
    store.save_round(1, day, 2, 10)
    store.save_round(1, day, 1, 5)
    row = store.get_round(1, day)
    assert row["picked"] == 3
    assert row["requests"] == 15


def test_recent_contents(store):
    action = store.claim_action(1, "comment", "BV1", "第一条")
    store.finish_action(action, "done", remote_id="1")
    assert store.recent_contents(1, "comment") == ["第一条"]


def test_claim_mention_dedupes(store):
    assert store.claim_mention(1, 9, "BV1", 456, "主人", "@恋恋 看看") is True
    assert store.claim_mention(1, 9, "BV1", 456, "主人", "@恋恋 看看") is False
    assert store.count_mentions_today(date.today().isoformat()) == 1


def test_mention_reply_roundtrip(store):
    store.claim_mention(1, 9, "BV1", 456, "主人", "@恋恋 看看")
    mention = store.mention_for_bvid("BV1")
    assert mention is not None
    assert mention["rpid"] == 456
    assert mention["name"] == "主人"
    assert store.mention_root("BV1") == 456
    store.mark_mention_replied(mention["msg_id"])
    assert store.mention_for_bvid("BV1") is None
    assert store.mention_root("BV1") == 456


def test_claim_comment_allows_three_per_target(store):
    ids = [store.claim_action(1, "comment", "BV1", f"第{i}条", limit=3) for i in range(4)]
    assert all(ids[:3])
    assert ids[3] is None
    assert store.count_live(1, "comment", "BV1") == 3


def test_reviewable_actions_requires_remote_id(store):
    first = store.claim_action(1, "comment", "BV1", "第一条")
    store.finish_action(first, "done", remote_id="321")
    second = store.claim_action(1, "comment", "BV2", "第二条")
    store.finish_action(second, "done")
    assert [a["target"] for a in store.reviewable_actions(1, "comment")] == ["BV1"]


# ---------- 配置与未登录路径 ----------

def test_load_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "missing.json")
    cfg = duty.load_config()
    assert cfg["max_videos"] == 6
    assert cfg["allow_comment"] is False


def test_load_config_ignores_wrong_types(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"max_videos": "many", "allow_like": "yes"}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cfg = duty.load_config()
    assert cfg["max_videos"] == 6
    assert cfg["allow_like"] is False


def test_run_round_without_credential(monkeypatch, tmp_path, store):
    monkeypatch.setattr(duty, "CREDENTIAL_PATH", tmp_path / "none.json")
    result = duty.run_round(store)
    assert result["ok"] is False
    assert result["reason"] == "not_logged_in"


def test_update_config_persists_switch(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"allow_like": false, "keywords": ["x"]}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cfg = duty.update_config({"allow_like": True})
    assert cfg["allow_like"] is True
    assert cfg["keywords"] == ["x"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["allow_like"] is True
    assert saved["max_videos"] == 6


def test_update_config_ignores_unknown_key(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    duty.update_config({"nope": 1})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "nope" not in saved


def test_parse_keywords_splits_and_dedupes():
    assert duty.parse_keywords("东方, 桌宠，AI 桌宠\n东方") == ["东方", "桌宠", "AI 桌宠"]


def test_is_bvid():
    assert is_bvid("BV1xx411c7mD")
    assert not is_bvid("bv1xx411c7mD")
    assert not is_bvid("BV123")
    assert not is_bvid("东方Project")


def test_split_keywords_separates_bv():
    bv, text = duty.split_keywords(["BV1xx411c7mD", "东方Project", "BV1yy411c7mD"])
    assert bv == ["BV1xx411c7mD", "BV1yy411c7mD"]
    assert text == ["东方Project"]


def test_normalize_keywords_filters_invalid():
    assert duty.normalize_keywords(["东方", "", "  ", "a" * 60, "东方"]) == ["东方"]


def test_normalize_keywords_keeps_bvid():
    assert duty.normalize_keywords(["BV1xx411c7mD"]) == ["BV1xx411c7mD"]


def test_normalize_keywords_rejects_non_list():
    assert duty.normalize_keywords("东方") == []


def test_run_round_rejects_blank_keyword(monkeypatch, tmp_path, store):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "config.json")
    result = duty.run_round(store, keyword="   ")
    assert result["ok"] is False
    assert result["reason"] == "invalid_keyword"


def test_parse_window_defaults_on_invalid():
    assert schedule.parse_window("bad") == (540, 1380)
    assert schedule.parse_window("23:00-09:00") == (540, 1380)


def test_next_trigger_inside_window():
    now = datetime(2026, 10, 21, 10, 0)
    assert schedule.next_trigger(now, 180, (540, 1380)) == datetime(2026, 10, 21, 13, 0)


def test_next_trigger_before_window():
    now = datetime(2026, 10, 21, 7, 0)
    assert schedule.next_trigger(now, 180, (540, 1380)) == datetime(2026, 10, 21, 10, 0)


def test_next_trigger_after_window():
    now = datetime(2026, 10, 21, 20, 0)
    assert schedule.next_trigger(now, 180, (540, 1380)) == datetime(2026, 10, 22, 9, 0)


def test_backoff_grows_and_caps():
    now = datetime(2026, 10, 21, 10, 0)
    assert schedule.backoff_at(now, 0, 5) == datetime(2026, 10, 21, 10, 5)
    assert schedule.backoff_at(now, 2, 5) == datetime(2026, 10, 21, 10, 20)
    assert schedule.backoff_at(now, 9, 5) == datetime(2026, 10, 21, 11, 0)


def test_parse_pause_windows_skips_invalid():
    assert schedule.parse_pause_windows("09:00-12:00,bad,14:00-18:00") == [(540, 720), (840, 1080)]


def test_is_paused_on_weekday():
    windows = [(540, 720), (840, 1080)]
    assert schedule.is_paused(datetime(2026, 10, 21, 10, 0), windows)
    assert not schedule.is_paused(datetime(2026, 10, 21, 12, 30), windows)
    assert schedule.is_paused(datetime(2026, 10, 21, 15, 0), windows)


def test_is_paused_skips_weekend():
    windows = [(540, 720), (840, 1080)]
    assert not schedule.is_paused(datetime(2026, 10, 24, 10, 0), windows)


def test_next_out_of_pause_returns_window_end():
    windows = [(540, 720), (840, 1080)]
    assert schedule.next_out_of_pause(datetime(2026, 10, 21, 10, 0), windows) \
        == datetime(2026, 10, 21, 12, 0)
    assert schedule.next_out_of_pause(datetime(2026, 10, 21, 12, 30), windows) \
        == datetime(2026, 10, 21, 12, 30)


def test_parse_keywords_limits_to_20():
    text = ",".join(f"k{i}" for i in range(30))
    assert len(duty.parse_keywords(text)) == 20


def test_set_keywords_persists(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cfg = duty.set_keywords("东方, 桌宠，AI 桌宠")
    assert cfg["keywords"] == ["东方", "桌宠", "AI 桌宠"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["keywords"] == ["东方", "桌宠", "AI 桌宠"]


def test_set_keywords_empty_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "config.json")
    assert duty.set_keywords("   ")["keywords"]


def test_set_dm_uid_rejects_non_numeric(monkeypatch, tmp_path):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "config.json")
    with pytest.raises(ValueError):
        duty.set_dm_uid("abc")


def test_set_dm_uid_rejects_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "config.json")
    with pytest.raises(ValueError):
        duty.set_dm_uid("0")


def test_set_dm_uid_persists(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    assert duty.set_dm_uid("12345")["dm_uid"] == 12345
    assert json.loads(path.read_text(encoding="utf-8"))["dm_uid"] == 12345
    assert duty.set_dm_uid("")["dm_uid"] == 0


def test_dm_without_uid_reports_error(monkeypatch, tmp_path, store):
    path = tmp_path / "config.json"
    path.write_text('{"allow_dm": true, "dm_uid": 0}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cred_path = tmp_path / "credential.json"
    Credential(sessdata="s", bili_jct="j", dedeuserid="9").save(cred_path)
    monkeypatch.setattr(duty, "CREDENTIAL_PATH", cred_path)
    result = duty.write_action(store, "dm", text="今天看了一个视频")
    assert result["ok"] is False
    assert result["reason"] == "dm_uid_missing"


def test_run_round_when_browse_disabled(monkeypatch, tmp_path, store):
    path = tmp_path / "config.json"
    path.write_text('{"allow_browse": false}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    monkeypatch.setattr(duty, "CREDENTIAL_PATH", tmp_path / "none.json")
    result = duty.run_round(store)
    assert result["ok"] is False
    assert result["reason"] == "disabled"
