"""B 站工具：签名、解析、内容过滤、写操作台账。"""

from __future__ import annotations

import base64
import io
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from pet.tools.bilibili import core, duty, filters, schedule
from pet.tools.bilibili import storage as bili_storage
from pet.tools import bilibili as bili_tool
from pet.tools.bilibili.core import (
    BiliClient,
    Credential,
    absolute_url,
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
        {"id": 1, "user": {"mid": 9, "nickname": "主人"},
         "item": {"uri": "https://www.bilibili.com/video/BV1j4411W7F7",
                  "source_id": 456, "source_content": "@恋恋 看看这个"}},
        {"id": 2, "user": {"mid": 9},
         "item": {"uri": "https://www.bilibili.com/video/BV", "source_content": "无"}},
    ]}
    items = parse_mentions(data)
    assert len(items) == 1
    assert items[0] == {"id": 1, "uid": 9, "name": "主人", "bvid": "BV1j4411W7F7",
                         "rpid": 456, "content": "@恋恋 看看这个"}


def test_parse_mentions_uses_source_id_as_rpid():
    data = {"items": [{
        "id": 7, "user": {"mid": 9, "nickname": "主人"},
        "item": {"uri": "https://www.bilibili.com/video/BV1j4411W7F7",
                 "root_id": 0, "source_id": 321793392864,
                 "source_content": "@恋恋 看看"},
    }]}
    assert parse_mentions(data)[0]["rpid"] == 321793392864


def test_parse_mentions_accepts_web_url():
    data = {"items": [{
        "id": 9, "user": {"mid": 9, "nickname": "主人"},
        "item": {"uri": "https://www.bilibili.com/video/BV1j4411W7F7",
                 "source_id": 321793392864, "source_content": "@恋恋 看看"},
    }]}
    item = parse_mentions(data)[0]
    assert item["bvid"] == "BV1j4411W7F7"
    assert item["rpid"] == 321793392864


def test_parse_mentions_prefers_thread_root():
    data = {"items": [{
        "id": 8, "user": {"mid": 9, "nickname": "主人"},
        "item": {"uri": "https://www.bilibili.com/video/BV1j4411W7F7",
                 "root_id": 111, "source_id": 222, "source_content": "@恋恋"},
    }]}
    assert parse_mentions(data)[0]["rpid"] == 111


def test_parse_view_tolerates_wrong_field_types():
    info = parse_view({"aid": "n/a", "owner": "up", "stat": [1], "desc": 5, "title": 7})
    assert (info["aid"], info["up"], info["play"]) == (0, "", 0)
    assert (info["title"], info["desc"]) == ("7", "5")


def test_parse_search_items_tolerates_wrong_types():
    data = {"result": [{"bvid": "BV1", "aid": "x", "play": None,
                        "pubdate": "y", "title": 5, "description": 6}]}
    item = parse_search_items(data)[0]
    assert (item["aid"], item["play"], item["pubdate"]) == (0, 0, 0)
    assert (item["title"], item["description"]) == ("5", "6")


def test_parse_mentions_skips_non_dict_and_bad_numbers():
    data = {"items": ["bad", {
        "item": {"uri": "https://www.bilibili.com/video/BV1xx411c7mD",
                 "root_id": "x"},
        "user": "u",
    }]}
    items = parse_mentions(data)
    assert len(items) == 1
    assert (items[0]["uid"], items[0]["rpid"]) == (0, 0)


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


def _json_client(handler):
    return BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                      interval=0.0, transport=httpx.MockTransport(handler))


def test_get_json_rejects_non_object_payload():
    def handler(request):
        if "finger/spi" in str(request.url):
            return httpx.Response(200, json={"data": {"b_3": "b3", "b_4": "b4"}})
        return httpx.Response(200, json=[1, 2, 3])

    client = _json_client(handler)
    with pytest.raises(core.BiliError) as err:
        client._get_json("https://i0.hdslb.com/a.json")
    client.close()
    assert err.value.kind == "parse"


def test_request_maps_timeout_to_timeout_kind():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = _json_client(handler)
    with pytest.raises(core.BiliError) as err:
        client._request("GET", "/x/web-interface/nav")
    client.close()
    assert err.value.kind == "timeout"


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
                      buvid4="b4", ac_time_value="a")
    cred.save(path)
    assert Credential.from_file(path) == cred


def test_credential_cookie_header_matches_bilibili_api_order():
    cred = Credential(sessdata="s", bili_jct="j", dedeuserid="9",
                      buvid3="b", buvid4="b4", ac_time_value="a")
    parts = cred.cookie_header().split("; ")
    assert parts == ["SESSDATA=s", "buvid3=b", "buvid4=b4", "bili_jct=j",
                     "DedeUserID=9", "ac_time_value=a"]


def test_credential_missing_fields(tmp_path):
    path = tmp_path / "credential.json"
    path.write_text('{"sessdata": "s"}', encoding="utf-8")
    assert Credential.from_file(path) is None


def test_credential_repr_hides_secrets():
    cred = Credential(sessdata="SECRET", bili_jct="CSRF", dedeuserid="9",
                      ac_time_value="AC")
    text = repr(cred)
    assert "SECRET" not in text
    assert "CSRF" not in text
    assert "AC" not in text
    assert "9" in text


# ---------- 错误映射 ----------

def test_mentions_requests_main_api_host(monkeypatch):
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    monkeypatch.setattr(core, "_buvid_pair", ("B3", "B4"))
    client = BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                        interval=0.0, transport=httpx.MockTransport(handler))
    assert client.mentions() == []
    client.close()
    assert seen == {"host": "api.bilibili.com", "path": "/x/msgfeed/at"}


def test_credential_path_under_app_dir():
    assert core.credential_path().parent == Path(core.app_dir())


def test_http_412_maps_to_risk():
    def handler(request):
        return httpx.Response(412, json={"code": -412, "message": "请求被拦截"})

    client = BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                        interval=0.0, transport=httpx.MockTransport(handler))
    with pytest.raises(core.BiliError) as exc:
        client.mentions()
    client.close()
    assert exc.value.kind == "risk"


def test_http_error_without_code_maps_to_network():
    def handler(request):
        return httpx.Response(500, text="oops")

    client = BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                        interval=0.0, transport=httpx.MockTransport(handler))
    with pytest.raises(core.BiliError) as exc:
        client.mentions()
    client.close()
    assert exc.value.kind == "network"


def test_absolute_url_forces_https():
    assert absolute_url("//i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert absolute_url("http://i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert absolute_url("https://i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert absolute_url("") == ""


def _receipt_client(monkeypatch, payload):
    def handler(request):
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(200, json={"code": 0, "data": {"wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB}.png",
            }}})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(core, "_buvid_pair", ("B3", "B4"))
    return BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                      interval=0.0, transport=httpx.MockTransport(handler))


def test_comment_without_rpid_raises(monkeypatch):
    client = _receipt_client(monkeypatch, {"code": 0, "data": {}})
    with pytest.raises(core.BiliError) as exc:
        client.comment(42, "测试")
    client.close()
    assert exc.value.kind == "receipt"


def test_dm_without_msg_key_raises(monkeypatch):
    client = _receipt_client(monkeypatch, {"code": 0, "data": {}})
    with pytest.raises(core.BiliError) as exc:
        client.dm(9, "在吗")
    client.close()
    assert exc.value.kind == "receipt"


def test_dm_returns_receipt(monkeypatch):
    client = _receipt_client(monkeypatch, {"code": 0, "data": {"msg_key": 123}})
    assert client.dm(9, "在吗")["msg_key"] == 123
    client.close()


# ---------- 评论请求 ----------

def test_comment_request_carries_web_params(monkeypatch):
    seen = {}

    def handler(request):
        path = request.url.path
        if path == "/x/frontend/finger/spi":
            return httpx.Response(200, json={"code": 0, "data": {"b_3": "B3", "b_4": "B4"}})
        if path == "/x/web-interface/nav":
            return httpx.Response(200, json={"code": 0, "data": {"wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB}.png",
            }}})
        if path == "/x/v2/reply/add":
            seen["query"] = dict(request.url.params)
            seen["body"] = dict(httpx.QueryParams(request.content.decode()))
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json={"code": 0, "data": {"rpid": 123}})
        return httpx.Response(404)

    monkeypatch.setattr(core, "_buvid_pair", None)
    client = BiliClient(Credential(sessdata="s", bili_jct="j", dedeuserid="9"),
                        interval=0.0, transport=httpx.MockTransport(handler))
    assert client.comment(42, "测试") == 123
    client.close()

    query = seen["query"]
    assert query["web_location"] == "1550101"
    assert query["dm_img_list"] == "[]"
    assert len(query["dm_img_str"]) == 2
    assert len(query["dm_cover_img_str"]) == 2
    assert "dm_img_inter" in query
    assert query["wts"] and query["w_rid"]

    body = seen["body"]
    assert body["oid"] == "42"
    assert body["statistics"] == '{"appId":100,"platform":5}'
    assert body["gaia_source"] == "main_web"
    assert body["csrf"] == body["csrf_token"] == "j"
    assert body["plat"] == "1"
    assert "buvid3=B3" in seen["cookie"]
    assert "buvid4=B4" in seen["cookie"]



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


def test_claim_action_without_limit_skips_dedupe(store):
    """私信目标恒定，靠每日配额限流，不做目标去重。"""
    assert store.claim_action(1, "dm", "9", limit=0) is not None
    assert store.claim_action(1, "dm", "9", limit=0) is not None
    assert store.count_live(1, "dm", "9") == 2


def test_count_today(store, monkeypatch):
    monkeypatch.setattr(bili_storage, "_now", lambda: "2026-10-21T10:00:00")
    monkeypatch.setattr(bili_storage, "_ago", lambda minutes: "2026-10-21T09:00:00")
    store.claim_action(1, "like", "BV1")
    store.claim_action(1, "like", "BV2")
    assert store.count_today(1, "like", "2026-10-21") == 2
    assert store.count_today(1, "coin", "2026-10-21") == 0


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


def test_add_mention_dedupes(store, monkeypatch):
    monkeypatch.setattr(bili_storage, "_now", lambda: "2026-10-21T10:00:00")
    store.add_mention(1, 9, "BV1", "标题", 456, "主人", "@恋恋 看看")
    assert store.has_mention(1)
    store.add_mention(1, 9, "BV1", "新标题", 456, "主人", "@恋恋 看看")
    rows = store.unreplied_mentions(9)
    assert len(rows) == 1
    assert rows[0]["title"] == "新标题"


def test_unreplied_mentions_hide_after_reply(store, monkeypatch):
    stamps = iter(["2026-10-21T10:00:00", "2026-10-21T10:01:00"])
    monkeypatch.setattr(bili_storage, "_now", lambda: next(stamps))
    store.add_mention(1, 9, "BV1", "标题", 456, "主人", "@恋恋 看看")
    store.add_mention(2, 9, "BV2", "标题", 457, "主人", "@恋恋 再看")
    assert [m["bvid"] for m in store.unreplied_mentions(9)] == ["BV2", "BV1"]
    store.mark_mention_replied(1)
    assert [m["bvid"] for m in store.unreplied_mentions(9)] == ["BV2"]
    assert len(store.unreplied_mentions(9, limit=1)) == 1


def test_mention_reply_roundtrip(store):
    store.add_mention(1, 9, "BV1", "标题", 456, "主人", "@恋恋 看看")
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


def test_finish_action_records_root(store):
    action = store.claim_action(1, "comment", "BV1", "x")
    store.finish_action(action, "done", remote_id="9", root=321)
    row = store.find_live_action(1, "comment", "BV1")
    assert row["remote_id"] == "9"
    assert row["root"] == 321


def test_unverified_comment_stays_reviewable(store):
    action = store.claim_action(1, "comment", "BV1", "x")
    store.finish_action(action, "unverified", remote_id="9", root=321)
    assert [a["id"] for a in store.reviewable_actions(1, "comment")] == [action]


def test_unknown_status_blocks_retry(store):
    action = store.claim_action(1, "comment", "BV1", "x")
    store.finish_action(action, "unknown", reason="receipt")
    assert store.find_live_action(1, "comment", "BV1")["status"] == "unknown"
    assert store.claim_action(1, "comment", "BV1", "x") is None


def test_guarded_write_receipt_marks_unknown(store):
    def boom():
        raise core.BiliError("receipt", "评论回执缺少 rpid")

    result = duty._guarded_write(store, 1, "comment", "BV1", "x", boom)
    assert result["ok"] is False
    assert result["reason"] == "no_receipt"
    assert store.find_live_action(1, "comment", "BV1")["status"] == "unknown"


def test_guarded_write_clear_failure_allows_retry(store):
    def boom():
        raise core.BiliError("api", "接口返回错误")

    result = duty._guarded_write(store, 1, "comment", "BV1", "x", boom)
    assert result["reason"] == "send_failed"
    assert store.claim_action(1, "comment", "BV1", "x") is not None


def test_guarded_write_timeout_marks_unknown(store):
    def boom():
        raise core.BiliError("timeout", "请求超时")

    result = duty._guarded_write(store, 1, "dm", "9", "晚安", boom)
    assert result["ok"] is False
    assert result["reason"] == "result_unknown"
    assert store.find_live_action(1, "dm", "9")["status"] == "unknown"
    assert store.claim_action(1, "dm", "9", "晚安") is None


def test_with_hint_attaches_tool_hint():
    payload = duty.with_hint({"error": "配置里没打开「评论」", "code": "disabled"})
    assert payload["hint"]
    assert "hint" not in duty.with_hint({"error": "未知错误", "code": "nope"})
    assert "hint" not in duty.with_hint({"headline": "成功"})


def test_claim_action_reclaims_stale_pending(store):
    action = store.claim_action(1, "like", "BV1")
    stale = bili_storage._ago(bili_storage._PENDING_TIMEOUT_MIN + 1)
    store._conn.execute("UPDATE bili_actions SET created_at = ? WHERE id = ?",
                        (stale, action))
    store._conn.commit()
    assert store.claim_action(1, "like", "BV1") is not None


def test_prune_drops_expired_rows(store):
    old = bili_storage._ago(bili_storage._RETENTION_DAYS * 24 * 60 + 60)
    store.mark_seen([
        {"bvid": "BV_OLD", "title": "t", "up": "u", "pubdate": 0,
         "verdict": "picked", "note": ""},
        {"bvid": "BV_NEW", "title": "t", "up": "u", "pubdate": 0,
         "verdict": "picked", "note": ""},
    ])
    store._conn.execute("UPDATE bili_seen SET seen_at = ? WHERE bvid = ?", (old, "BV_OLD"))
    store._conn.commit()
    action = store.claim_action(1, "comment", "BV1", "x")
    store.finish_action(action, "done", remote_id="1")
    store._conn.execute("UPDATE bili_actions SET created_at = ?", (old,))
    store._conn.commit()
    store.prune()
    assert not store.is_seen("BV_OLD")
    assert store.is_seen("BV_NEW")
    assert store.recent_contents(1, "comment") == []


# ---------- 配置与未登录路径 ----------

def test_load_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "missing.json")
    cfg = duty.load_config()
    assert cfg["per_run"] == 2
    assert cfg["allow_comment"] is False


def test_load_config_ignores_wrong_types(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"per_run": "many", "allow_like": "yes"}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cfg = duty.load_config()
    assert cfg["per_run"] == 2
    assert cfg["allow_like"] is False


def test_run_round_without_credential(monkeypatch, tmp_path, store):
    monkeypatch.setattr(duty, "credential_path", lambda: tmp_path / "none.json")
    result = duty.run_round(store)
    assert result["code"] == "not_logged_in"


def test_update_config_persists_switch(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"allow_like": false, "keywords": ["x"]}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cfg = duty.update_config({"allow_like": True})
    assert cfg["allow_like"] is True
    assert cfg["keywords"] == ["x"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["allow_like"] is True
    assert saved["per_run"] == 2


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
    assert result["code"] == "invalid_keyword"


def test_parse_request_keeps_bv_beyond_keyword_cap():
    bv, words = duty._parse_request("手抓羊肉,兰州,羊脖子,BV1spHA66Exo")
    assert bv == ["BV1spHA66Exo"]
    assert words == ["手抓羊肉", "兰州", "羊脖子"]


def test_pick_chosen_keeps_requested_bv():
    candidates = [{"bvid": "BV1"}, {"bvid": "BV2"}, {"bvid": "BV3"}]
    assert duty._pick_chosen(candidates, ["BV3"], 1) == [{"bvid": "BV3"}]


def test_pick_chosen_random_without_bv():
    candidates = [{"bvid": f"BV{i}"} for i in range(5)]
    assert len(duty._pick_chosen(candidates, [], 2)) == 2


def test_collect_skips_search_when_bv_given(monkeypatch):
    searched = []
    monkeypatch.setattr(duty, "_search_keyword",
                        lambda *a, **k: searched.append("search") or ([], 0))
    monkeypatch.setattr(duty, "_add_candidate",
                        lambda client, cands, skipped, bvid, has_cover, kw="":
                        cands.append({"bvid": bvid}))
    monkeypatch.setattr(duty.TOOL_CTX, "has_vision", lambda: False)
    candidates, meta, _ = duty._collect(
        SimpleNamespace(requests=0), dict(duty._DEFAULTS), None,
        ["手抓羊肉", "BV1spHA66Exo"])
    assert [c["bvid"] for c in candidates] == ["BV1spHA66Exo"]
    assert searched == []


def test_collect_stops_at_per_run(monkeypatch):
    needs = []
    monkeypatch.setattr(
        duty, "_search_keyword",
        lambda *a, **k: needs.append(a[6]) or ([f"BV{i}" for i in range(6)], 0))
    monkeypatch.setattr(duty, "_add_candidate",
                        lambda client, cands, skipped, bvid, has_cover, kw="":
                        cands.append({"bvid": bvid}))
    monkeypatch.setattr(duty.TOOL_CTX, "has_vision", lambda: False)
    cfg = dict(duty._DEFAULTS, per_run=2)
    candidates, _, _ = duty._collect(
        SimpleNamespace(requests=0), cfg, None, ["狗"])
    assert [c["bvid"] for c in candidates] == ["BV0", "BV1"]
    assert needs == [2]


def test_collect_bv_mode_collects_all_targets(monkeypatch):
    monkeypatch.setattr(duty, "_search_keyword",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no search")))
    monkeypatch.setattr(duty, "_add_candidate",
                        lambda client, cands, skipped, bvid, has_cover, kw="":
                        cands.append({"bvid": bvid}))
    monkeypatch.setattr(duty.TOOL_CTX, "has_vision", lambda: False)
    cfg = dict(duty._DEFAULTS, per_run=2)
    candidates, _, _ = duty._collect(
        SimpleNamespace(requests=0), cfg, None,
        ["BV1spHA66Exo", "BV1Lmbj6jEq6", "BV1tDnm6KE9L"])
    assert [c["bvid"] for c in candidates] == [
        "BV1spHA66Exo", "BV1Lmbj6jEq6", "BV1tDnm6KE9L"]


def test_browse_accepts_bvid_param(monkeypatch, store):
    seen = {}
    monkeypatch.setattr(bili_tool, "_storage", store)
    monkeypatch.setattr(
        duty, "run_round",
        lambda storage, keyword="": seen.update(kw=keyword) or {"headline": "ok"})
    assert bili_tool._browse(bvid="BV1spHA66Exo")["headline"] == "ok"
    assert seen["kw"] == "BV1spHA66Exo"
    bili_tool._browse(keyword="狗")
    assert seen["kw"] == "狗"
    result = bili_tool._browse(keyword="手抓羊肉", bvid="BV1spHA66Exo")
    assert seen["kw"] == "BV1spHA66Exo"
    assert result["hint"]


def test_browse_rejects_invalid_bvid(monkeypatch, store):
    called = []
    monkeypatch.setattr(bili_tool, "_storage", store)
    monkeypatch.setattr(duty, "run_round",
                        lambda *a, **k: called.append(1) or {"headline": "ok"})
    result = bili_tool._browse(bvid="not-a-bv", keyword="狗")
    assert result["code"] == "invalid_bvid"
    assert result["hint"]
    assert called == []


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


def test_parse_pause_windows_accepts_midnight_end():
    assert schedule.parse_pause_windows("09:00-24:00") == [(540, 1440)]


def test_invalid_pause_parts_reports_bad_entries():
    assert schedule.invalid_pause_parts("09:00-12:00, bad") == ["bad"]
    assert schedule.invalid_pause_parts("") == []


def test_next_out_of_pause_supports_midnight_end():
    windows = schedule.parse_pause_windows("09:00-24:00")
    assert schedule.next_out_of_pause(datetime(2026, 10, 21, 22, 0), windows) \
        == datetime(2026, 10, 22, 0, 0)


def test_parse_pause_windows_accepts_cross_midnight():
    assert schedule.parse_pause_windows("22:00-08:00") == [(1320, 480)]


def test_is_paused_across_midnight():
    windows = schedule.parse_pause_windows("22:00-08:00")
    assert schedule.is_paused(datetime(2026, 10, 21, 23, 0), windows)
    assert schedule.is_paused(datetime(2026, 10, 21, 7, 0), windows)
    assert not schedule.is_paused(datetime(2026, 10, 21, 12, 0), windows)


def test_next_out_of_pause_across_midnight():
    windows = schedule.parse_pause_windows("22:00-08:00")
    assert schedule.next_out_of_pause(datetime(2026, 10, 21, 23, 0), windows) \
        == datetime(2026, 10, 22, 8, 0)
    assert schedule.next_out_of_pause(datetime(2026, 10, 22, 7, 0), windows) \
        == datetime(2026, 10, 22, 8, 0)


def test_next_out_of_pause_takes_latest_end_of_overlaps():
    windows = schedule.parse_pause_windows("09:00-12:00,10:00-14:00")
    assert schedule.next_out_of_pause(datetime(2026, 10, 21, 10, 30), windows) \
        == datetime(2026, 10, 21, 14, 0)


def _boom(*_args, **_kwargs):
    raise ValueError("boom")


def test_tick_rearms_after_error(monkeypatch):
    armed = []
    monkeypatch.setattr(schedule, "load_config", lambda: dict(duty._DEFAULTS))
    monkeypatch.setattr(schedule, "_advance", _boom)
    monkeypatch.setattr(schedule, "_arm", armed.append)
    schedule._tick()
    assert armed


def test_interact_appends_mentions(monkeypatch):
    seen = {}
    monkeypatch.setattr(schedule, "schedule_text", lambda: "到点了")
    monkeypatch.setattr(schedule, "_fetch_mentions", lambda cfg: [
        {"name": "主人", "bvid": "BV1", "title": "小狗"}])
    monkeypatch.setattr(schedule.TOOL_CTX, "request_interact",
                        lambda hint, **kwargs: seen.setdefault("hint", hint))
    schedule._interact({"mention_enabled": True})
    assert "BV1" in seen["hint"]
    assert "主人" in seen["hint"]


def test_interact_skips_mentions_when_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(schedule, "schedule_text", lambda: "到点了")
    monkeypatch.setattr(schedule, "_fetch_mentions",
                        lambda cfg: called.append(1) or [])
    monkeypatch.setattr(schedule.TOOL_CTX, "request_interact",
                        lambda hint, **kwargs: None)
    schedule._interact({"mention_enabled": False})
    assert called == []


class _InlineThread:
    """把 start() 变成同步调用，便于断言触发目标。"""

    def __init__(self, target=None, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class _InlineThreading:
    Thread = _InlineThread


def test_trigger_now_ignores_switch(monkeypatch):
    seen = []
    monkeypatch.setattr(schedule, "threading", _InlineThreading)
    monkeypatch.setattr(schedule, "load_config",
                        lambda: {"schedule_enabled": False, "mention_enabled": False})
    monkeypatch.setattr(schedule, "_interact", seen.append)
    schedule.trigger_now()
    assert seen == [{"schedule_enabled": False, "mention_enabled": False}]


class _MentionClient:
    def __init__(self, items):
        self._items = items

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mentions(self, limit: int = 10):
        return list(self._items)

    def view(self, bvid: str):
        return {"bvid": bvid, "title": "标题"}


def test_fetch_mentions_keeps_unreplied(monkeypatch, store):
    """未回复的 @ 每轮都带出来，回复后不再出现。"""
    monkeypatch.setattr(schedule, "_storage", store)
    monkeypatch.setattr(schedule, "load_credential",
                        lambda: Credential("s", "j", "9"))
    monkeypatch.setattr(schedule, "BiliClient", lambda *a, **k: _MentionClient([
        {"id": 1, "uid": 9, "name": "主人", "bvid": "BV1",
         "rpid": 456, "content": "@恋恋"},
    ]))
    cfg = dict(duty._DEFAULTS, dm_uid=9, mention_per_day=3)
    assert [m["bvid"] for m in schedule._fetch_mentions(cfg)] == ["BV1"]
    assert [m["bvid"] for m in schedule._fetch_mentions(cfg)] == ["BV1"]
    store.mark_mention_replied(1)
    assert schedule._fetch_mentions(cfg) == []


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


def test_set_interval_minutes_persists(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"schedule_interval_minutes": 300}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    assert duty.set_interval_minutes("240")["schedule_interval_minutes"] == 240
    assert json.loads(path.read_text(encoding="utf-8"))["schedule_interval_minutes"] == 240
    assert duty.set_interval_minutes("  ")["schedule_interval_minutes"] == 180


@pytest.mark.parametrize("text", ["9", "1441", "abc", "-60", "60.5"])
def test_set_interval_minutes_rejects_invalid(monkeypatch, tmp_path, text):
    monkeypatch.setattr(duty, "_CONFIG_FILE", tmp_path / "config.json")
    with pytest.raises(ValueError):
        duty.set_interval_minutes(text)


def test_dm_without_uid_reports_error(monkeypatch, tmp_path, store):
    path = tmp_path / "config.json"
    path.write_text('{"allow_dm": true, "dm_uid": 0}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    cred_path = tmp_path / "credential.json"
    Credential(sessdata="s", bili_jct="j", dedeuserid="9").save(cred_path)
    monkeypatch.setattr(duty, "credential_path", lambda: cred_path)
    result = duty.write_action(store, "dm", text="今天看了一个视频")
    assert result["code"] == "dm_uid_missing"


def test_run_round_when_browse_disabled(monkeypatch, tmp_path, store):
    path = tmp_path / "config.json"
    path.write_text('{"allow_browse": false}', encoding="utf-8")
    monkeypatch.setattr(duty, "_CONFIG_FILE", path)
    monkeypatch.setattr(duty, "credential_path", lambda: tmp_path / "none.json")
    result = duty.run_round(store)
    assert result["code"] == "disabled"


def test_reply_hidden_detects_avalon_state():
    assert duty._reply_hidden({"state": 17})
    assert duty._reply_hidden({"invisible": True})
    assert not duty._reply_hidden({"state": 0, "invisible": False})
