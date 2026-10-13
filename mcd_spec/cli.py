"""Command line for mcd-spec."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mcd_spec.catalog import DemoCatalog, McpCatalog
from mcd_spec.client import McpClient, McpError
from mcd_spec.compiler import compile_spec
from mcd_spec.render import render_json, render_text
from mcd_spec.spec import Spec, SpecError, apply_overrides, parse_file, split_terms


DEFAULT_URL = "https://mcp.mcd.cn"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcd-spec",
        description="把预算、热量、忌口和优惠券编译成一份已经核过价的麦当劳购物车。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    compile_parser = sub.add_parser("compile", help="按规格编译购物车")
    compile_parser.add_argument("spec_file", nargs="?", help="JSON 或按行书写的规格文件")
    compile_parser.add_argument("--budget", type=float, help="预算上限，单位元")
    compile_parser.add_argument("--max-kcal", type=int, help="热量上限，单位 kcal")
    compile_parser.add_argument("--avoid", help="忌口，用逗号分隔，例如 可乐,酸黄瓜")
    compile_parser.add_argument("--drink", help="饮料偏好，例如 无糖")
    compile_parser.add_argument("--want", help="想吃的关键词，用逗号分隔")
    compile_parser.add_argument("--quantity", type=int, help="份数，默认 1")
    compile_parser.add_argument("--scene", choices=["pickup", "drive", "delivery"], help="pickup、drive 或 delivery")
    compile_parser.add_argument("--city", help="城市，到店搜索时使用")
    compile_parser.add_argument("--place", help="地点关键词，到店搜索时使用")
    compile_parser.add_argument("--store-code", help="直接指定门店编码")
    compile_parser.add_argument("--be-code", help="直接指定 BE 编码")
    compile_parser.add_argument("--address-id", help="外送地址编号")
    compile_parser.add_argument("--no-coupon", action="store_true", help="不使用门店优惠券")
    compile_parser.add_argument("--demo", action="store_true", help="用离线示例数据运行，不访问麦当劳")
    compile_parser.add_argument("--json", action="store_true", help="输出 JSON")
    compile_parser.add_argument("--limit", type=int, default=3, help="最多展示几个方案，默认 3")

    doctor = sub.add_parser("doctor", help="检查 Token 能否连通麦当劳 MCP")
    doctor.add_argument("--url", default=None, help="MCP 地址，默认 https://mcp.mcd.cn 或环境变量 MCD_MCP_URL")

    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args)
        return _compile(args)
    except (SpecError, McpError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def _compile(args) -> int:
    spec = Spec()
    if args.spec_file:
        spec = parse_file(Path(args.spec_file))
    spec = apply_overrides(spec, _flag_overrides(args))
    spec.validate(demo=args.demo)
    if args.limit < 1:
        raise SpecError("--limit 至少为 1")
    catalog = DemoCatalog() if args.demo else McpCatalog(_client(args))
    result = compile_spec(spec, catalog, limit=args.limit)
    text = render_json(result) if args.json else render_text(result)
    sys.stdout.write(text)
    return 0


def _doctor(args) -> int:
    client = _client(args)
    payload = client.call_tool("now-time-info", {})
    formatted = ""
    if isinstance(payload, dict):
        formatted = str(payload.get("formatted") or payload.get("datetime") or "")
    if not formatted:
        formatted = "已连通"
    print(f"麦当劳 MCP 已连通，服务器时间 {formatted}")
    return 0


def _client(args) -> McpClient:
    token = os.environ.get("MCD_MCP_TOKEN", "")
    url = getattr(args, "url", None) or os.environ.get("MCD_MCP_URL") or DEFAULT_URL
    return McpClient(url, token)


def _flag_overrides(args) -> dict:
    overrides = {}
    if args.budget is not None:
        overrides["budget"] = args.budget
    if args.max_kcal is not None:
        overrides["maxKcal"] = args.max_kcal
    if args.avoid is not None:
        overrides["avoid"] = split_terms(args.avoid)
    if args.drink is not None:
        overrides["drink"] = args.drink
    if args.want is not None:
        overrides["want"] = split_terms(args.want)
    if args.quantity is not None:
        overrides["quantity"] = args.quantity
    if args.scene is not None:
        overrides["scene"] = args.scene
    if args.city is not None:
        overrides["city"] = args.city
    if args.place is not None:
        overrides["place"] = args.place
    if args.store_code is not None:
        overrides["storeCode"] = args.store_code
    if args.be_code is not None:
        overrides["beCode"] = args.be_code
    if args.address_id is not None:
        overrides["addressId"] = args.address_id
    if args.no_coupon:
        overrides["useCoupon"] = False
    return overrides


