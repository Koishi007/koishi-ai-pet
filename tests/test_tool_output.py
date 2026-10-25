"""工具输出契约：schema 校验、当轮渲染、历史记录与观测统计。"""

import pytest

from pet.tools.output import (IMAGES_SCHEMA, PAGINATION_SCHEMA, ImageBlock,
                              TextBlock, ToolOutputDefinition, render_record,
                              render_turn, validate)

WEATHER_SCHEMA = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline"},
        "body":     {"type": "string", "x-role": "body"},
        "data":     {"type": "object", "x-role": "data"},
        "pagination": {**PAGINATION_SCHEMA},
        "images":   IMAGES_SCHEMA,
        "agent_hint": {"type": "string", "x-role": "hint"},
    },
}


def definition() -> ToolOutputDefinition:
    return ToolOutputDefinition(schema=WEATHER_SCHEMA)


class TestValidate:
    def test_ok(self):
        value = {"headline": "深圳 晴", "data": {"temperature": 29.6}}
        assert validate(definition(), value) == []

    def test_missing_required(self):
        problems = validate(definition(), {"body": "正文"})
        assert any("headline" in p for p in problems)

    def test_wrong_type(self):
        problems = validate(definition(), {"headline": 123})
        assert any("期望 string" in p for p in problems)

    def test_nested_items(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "x-role": "data",
                          "items": {"type": "object",
                                    "properties": {"name": {"type": "string"}}}},
            },
        }
        problems = validate(ToolOutputDefinition(schema=schema),
                            {"items": [{"name": "ok"}, {"name": 1}]})
        assert any("items[1].name" in p for p in problems)

    def test_long_headline_is_not_violation(self):
        assert validate(definition(), {"headline": "长" * 500}) == []

    def test_enum(self):
        schema = {"type": "object",
                  "properties": {"kind": {"type": "string", "enum": ["a", "b"]}}}
        assert validate(ToolOutputDefinition(schema=schema), {"kind": "c"})

    def test_additional_properties_false(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"headline": {"type": "string", "x-role": "headline"}}}
        problems = validate(ToolOutputDefinition(schema=schema),
                            {"headline": "x", "extra": 1})
        assert any("未在 schema 中声明" in p for p in problems)


class TestRenderTurn:
    def test_role_order_fixed(self):
        value = {"agent_hint": "下一步", "data": {"a": 1},
                 "body": "正文", "headline": "结论"}
        blocks = render_turn(definition(), value)
        assert [b.text for b in blocks] == ["结论", "正文", "a: 1", "下一步"]

    def test_data_flattened(self):
        blocks = render_turn(definition(), {"data": {"memory": {"used": "4G"}}})
        assert blocks[0].text == "memory.used: 4G"
        assert blocks[0].role == "data"

    def test_undeclared_fields_go_to_data(self):
        blocks = render_turn(definition(), {"headline": "结论", "extra": 1})
        assert any("extra: 1" in b.text for b in blocks)

    def test_pagination_hint(self):
        value = {"pagination": {"page": 2, "total_items": 50, "has_next": True}}
        blocks = render_turn(definition(), value)
        assert blocks[-1].role == "hint"
        assert "page=3" in blocks[-1].text

    def test_no_hint_without_next(self):
        value = {"pagination": {"page": 3, "has_next": False}}
        assert render_turn(definition(), value) == []

    def test_images(self):
        value = {"images": [{"base64": "AAA", "mime": "image/png", "caption": "截图"}]}
        blocks = render_turn(definition(), value)
        assert isinstance(blocks[0], ImageBlock)
        assert blocks[0].caption == "截图"

    def test_suppress_not_rendered(self):
        blocks = render_turn(definition(), {"pagination": {"page": 1, "page_size": 20}})
        assert all("page_size" not in b.text for b in blocks)

    def test_renderer_override(self):
        custom = ToolOutputDefinition(schema={}, renderer=lambda v: [TextBlock("自定义")])
        assert render_turn(custom, {"x": 1})[0].text == "自定义"

    def test_malformed_pagination_not_rendered(self):
        blocks = render_turn(definition(), {"headline": "结论", "pagination": "坏值"})
        assert [b.text for b in blocks] == ["结论"]

    def test_headline_is_primary_block(self):
        blocks = render_turn(definition(), {"headline": "结论"})
        assert [b.role for b in blocks] == ["primary"]


class TestRenderRecord:
    def test_headline(self):
        assert render_record(definition(), {"headline": "深圳 晴"}) == "深圳 晴"

    def test_appends_more_hint(self):
        value = {"headline": "读取网页", "pagination": {"has_next": True}}
        assert render_record(definition(), value).endswith("还有更多")

    def test_truncates(self):
        value = {"headline": "长" * 200}
        assert len(render_record(definition(), value)) == 120

    def test_missing_headline(self):
        assert render_record(definition(), {"body": "正文"}) == ""


class TestDeclaredTools:
    """已声明 output 的工具：规范值与 schema 必须对得上。"""

    def test_file_ops_read_file(self):
        from pet.tools.file_ops import READ_FILE_OUTPUT

        definition = ToolOutputDefinition(schema=READ_FILE_OUTPUT)
        value = {"headline": "读取文件 a.md（offset=0，100 字符，还有更多）",
                 "body": "第一行", "agent_hint": "用 offset=100 继续读取"}
        assert validate(definition, value) == []
        blocks = render_turn(definition, value)
        assert blocks[0].text == value["headline"]
        assert blocks[-1].role == "hint"
        assert render_record(definition, value).startswith("读取文件 a.md")

    def test_browser_screenshot(self):
        from pet.tools.browser import SCREENSHOT_OUTPUT

        definition = ToolOutputDefinition(schema=SCREENSHOT_OUTPUT)
        value = {"headline": "截图网页 https://example.com",
                 "images": [{"base64": "AAA", "mime": "image/png", "caption": "截图"}]}
        assert validate(definition, value) == []
        assert isinstance(render_turn(definition, value)[-1], ImageBlock)

    def test_weather_current(self):
        from pet.tools.weather import CURRENT_OUTPUT

        definition = ToolOutputDefinition(schema=CURRENT_OUTPUT)
        value = {"headline": "深圳 晴，29.6°C（体感 31°C）",
                 "body": "湿度 62%，风速 12 km/h\n未来预报：",
                 "data": {"city": "深圳", "temperature": 29.6}}
        assert validate(definition, value) == []
        blocks = render_turn(definition, value)
        assert blocks[0].text == value["headline"]
        assert blocks[-1].role == "data"
        assert render_record(definition, value).startswith("深圳 晴")

    def test_system_monitor_overview(self):
        from pet.tools.system_monitor import WITH_DATA_OUTPUT

        definition = ToolOutputDefinition(schema=WITH_DATA_OUTPUT)
        value = {"headline": "CPU 12% (8核) | 内存 71% (15.7G/22G) | 磁盘 60%",
                 "data": {"cpu_percent": 12, "memory_percent": 71}}
        assert validate(definition, value) == []
        assert render_turn(definition, value)[-1].role == "data"

    def test_tool_search_hint_role(self):
        from pet.tools.registry import SEARCH_OUTPUT

        definition = ToolOutputDefinition(schema=SEARCH_OUTPUT)
        value = {"headline": "搜到 1 个：weather",
                 "body": "weather（info）：天气查询\n  - weather__get_current：查询当前天气",
                 "agent_hint": "已命中：weather__get_current。下一轮直接调用。"}
        assert validate(definition, value) == []
        blocks = render_turn(definition, value)
        assert blocks[0].text == value["headline"]
        assert blocks[-1].role == "hint"
        assert render_record(definition, value) == "搜到 1 个：weather"

    def test_knowledge_list_pagination(self):
        from pet.tools.knowledge import LIST_OUTPUT

        definition = ToolOutputDefinition(schema=LIST_OUTPUT)
        value = {"headline": "知识库共 30 条，第 1/2 页",
                 "body": "#1 笔记",
                 "pagination": {"page": 1, "page_size": 20,
                                "total_items": 30, "has_next": True}}
        assert validate(definition, value) == []
        blocks = render_turn(definition, value)
        assert blocks[-1].role == "hint"
        assert "page=2" in blocks[-1].text
        assert render_record(definition, value).endswith("还有更多")
