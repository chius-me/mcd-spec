import json
import unittest

from mcd_spec.catalog import McpCatalog
from mcd_spec.client import McpClient, McpError, unwrap_tool_result
from mcd_spec.spec import Spec


class FakeTransport:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, body):
        payload = json.loads(body.decode("utf-8"))
        self.calls.append((payload, headers))
        method = payload["method"]
        if method == "initialize":
            response = {"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-06-18"}}
            return 200, {"content-type": "application/json", "mcp-session-id": "sess-1"}, _dump(response)
        if method == "notifications/initialized":
            return 202, {"content-type": "application/json"}, b""
        if method == "tools/call":
            tool_payload = {
                "success": True,
                "code": 200,
                "message": "请求成功",
                "data": {"formatted": "2026-10-13 16:00:00"},
            }
            message = {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(tool_payload, ensure_ascii=False)}]},
            }
            sse = "event: message\ndata: " + json.dumps(message, ensure_ascii=False) + "\n\n"
            return 200, {"content-type": "text/event-stream", "mcp-session-id": "sess-1"}, sse.encode()
        raise AssertionError(method)


class ClientTest(unittest.TestCase):
    def test_initialize_then_unwrap_sse_tool_result(self):
        transport = FakeTransport()
        client = McpClient("https://mcp.mcd.cn", "test-token", transport)
        payload = client.call_tool("now-time-info", {})
        self.assertEqual(payload["formatted"], "2026-10-13 16:00:00")
        methods = [call[0]["method"] for call in transport.calls]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/call"])
        tool_headers = transport.calls[-1][1]
        self.assertEqual(tool_headers["Mcp-Session-Id"], "sess-1")
        self.assertEqual(tool_headers["Authorization"], "Bearer test-token")
        self.assertNotIn("test-token", transport.calls[-1][0]["params"]["arguments"])

    def test_missing_token_does_not_need_the_network(self):
        with self.assertRaises(McpError):
            McpClient("https://mcp.mcd.cn", "  ")

    def test_failed_business_payload(self):
        with self.assertRaises(McpError):
            unwrap_tool_result({
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({"success": False, "message": "门店不可用"}),
                    }],
                },
            })

    def test_markdown_wrapper_unwraps_the_business_object(self):
        text = (
            "# API Response Information\n\n"
            "## Original Response\n\n"
            '{"success":true,"code":200,"message":"请求成功",'
            '"data":{"formatted":"2026-10-13 16:46:24"}}'
            "## 展示结果时不要省略字段"
        )
        payload = unwrap_tool_result({
            "result": {"content": [{"type": "text", "text": text}]},
        })
        self.assertEqual(payload["formatted"], "2026-10-13 16:46:24")


class CatalogContractTest(unittest.TestCase):
    def test_pickup_search_arguments(self):
        client = ScriptedClient({
            "query-nearby-stores": [{
                "storeCode": "S1",
                "beCode": "B1",
                "storeName": "测试餐厅",
                "address": "某路",
                "distance": "300米",
                "businessStatus": True,
            }],
        })
        catalog = McpCatalog(client)
        spec = Spec(scene="pickup", city="上海", place="人民广场")
        store = catalog.resolve_store(spec)
        self.assertEqual(store.store_code, "S1")
        self.assertEqual(store.distance, "300米")
        name, args = client.calls[0]
        self.assertEqual(name, "query-nearby-stores")
        self.assertEqual(args, {
            "searchType": 2,
            "beType": 1,
            "city": "上海",
            "keyword": "人民广场",
        })
        self.assertEqual(catalog.context(store, spec), {
            "storeCode": "S1",
            "beCode": "B1",
            "orderType": 1,
            "beType": 1,
        })
        numeric = ScriptedClient({
            "query-nearby-stores": [{
                "storeCode": "S2",
                "storeName": "数字距离",
                "distance": 144,
                "businessStatus": True,
            }],
        })
        numbered = McpCatalog(numeric).resolve_store(spec)
        self.assertEqual(numbered.distance, "144米")
        self.assertEqual(numbered.be_code, "")
        self.assertNotIn("beCode", McpCatalog(numeric).context(numbered, spec))

    def test_multiple_addresses_do_not_leak_phone_numbers(self):
        client = ScriptedClient({
            "delivery-query-addresses": {
                "addresses": [
                    {"addressId": "a1", "fullAddress": "甲小区", "phone": "13800000000"},
                    {"addressId": "a2", "fullAddress": "乙小区", "phone": "13900000000"},
                ],
            },
        })
        with self.assertRaises(McpError) as caught:
            McpCatalog(client).resolve_store(Spec(scene="delivery"))
        message = str(caught.exception)
        self.assertIn("a1", message)
        self.assertIn("甲小区", message)
        self.assertNotIn("13800000000", message)

    def test_explicit_store_does_not_search(self):
        client = ScriptedClient({})
        store = McpCatalog(client).resolve_store(Spec(store_code="S9", be_code="B9"))
        self.assertEqual(store.name, "指定门店")
        self.assertEqual(client.calls, [])
        bare = McpCatalog(ScriptedClient({})).resolve_store(Spec(store_code="S9"))
        self.assertEqual(bare.store_code, "S9")
        self.assertEqual(bare.be_code, "")


class ScriptedClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name not in self.responses:
            raise AssertionError(name)
        return self.responses[name]


def _dump(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
