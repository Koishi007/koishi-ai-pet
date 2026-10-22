"""gen_docs 对 output= 的静态解析：字面量、常量名与 ** 展开。"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gen_docs  # noqa: E402

SOURCE = '''
PAGINATION_SCHEMA = {
    "type": "object",
    "x-role": "pagination",
    "properties": {
        "page": {"type": "integer", "x-role": "suppress", "description": "当前页码"},
        "has_next": {"type": "boolean", "x-role": "suppress", "description": "是否还有下一页"},
    },
}

READ_FILE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "一句话结论"},
        "body": {"type": "string", "x-role": "body", "description": "正文"},
        "pagination": {**PAGINATION_SCHEMA},
    },
}

registry.add_method(
    "file_ops", "read_file", "读取文件", handler=read_file,
    output=ToolOutputDefinition(schema=READ_FILE_OUTPUT),
)
'''


def parse(source: str = SOURCE) -> list[dict]:
    tree = ast.parse(source)
    return gen_docs._collect_methods(tree)


class TestParseOutput:
    def test_fields_parsed(self):
        methods = parse()
        assert len(methods) == 1
        fields = {f["name"]: f for f in methods[0]["output"]}
        assert set(fields) == {"headline", "body", "pagination"}

    def test_required_and_role(self):
        fields = {f["name"]: f for f in parse()[0]["output"]}
        assert fields["headline"]["required"] is True
        assert fields["headline"]["role"] == "headline"
        assert fields["body"]["role"] == "body"
        assert fields["body"]["required"] is False

    def test_spread_constant_keeps_role(self):
        fields = {f["name"]: f for f in parse()[0]["output"]}
        assert fields["pagination"]["role"] == "pagination"
        assert fields["pagination"]["type"] == "object"

    def test_description_kept(self):
        fields = {f["name"]: f for f in parse()[0]["output"]}
        assert fields["headline"]["description"] == "一句话结论"

    def test_inline_schema(self):
        source = '''
registry.add_method(
    "demo", "ping", "ping", handler=ping,
    output=ToolOutputDefinition(schema={
        "type": "object",
        "properties": {"headline": {"type": "string", "x-role": "headline"}},
    }),
)
'''
        fields = parse(source)[0]["output"]
        assert [f["name"] for f in fields] == ["headline"]

    def test_function_call_not_parsed(self):
        source = '''
registry.add_method(
    "demo", "ping", "ping", handler=ping,
    output=ToolOutputDefinition(schema=build_schema()),
)
'''
        method = parse(source)[0]
        assert method["has_output"] is True
        assert method["output"] == []


class TestRealTools:
    def test_every_method_declares_output(self):
        """阶段 4 之后，所有注册方法都应有输出契约。"""
        data = gen_docs.load_tools()
        missing = [
            f"{tool['name']}__{method['name']}"
            for tool in data["tools"] + data["meta_tools"]
            for method in tool["methods"]
            if not method.get("has_output")
        ]
        assert not missing

    def test_declared_output_always_parsable(self):
        """声明了 output 的方法必须能静态解析出字段，否则文档会静默缺列。"""
        data = gen_docs.load_tools()
        bad = [
            f"{tool['name']}__{method['name']}"
            for tool in data["tools"] + data["meta_tools"]
            for method in tool["methods"]
            if method.get("has_output") and not method.get("output")
        ]
        assert not bad
