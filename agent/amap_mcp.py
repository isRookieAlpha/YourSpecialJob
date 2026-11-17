# -*- coding: utf-8 -*-
"""
高德 MCP 客户端：入库时批量计算通勤（距离 / 骑行 / 公共交通 / 驾车）。

用户确认：只用高德 MCP，不降级 REST；MCP 不可用时由调用方提示
"高德 MCP 不可用，兼职通勤暂无法计算"。

实现要点：
- 包名 `@amap/amap-maps-mcp-server`（`@amap/amap-maps` 不存在）
- MCP stdio 子进程在 Windows 需要 ProactorEventLoop → 独立线程（复用 mcp_wechat 模式）
- 持久会话（client.session + load_mcp_tools），一批地点只启动一次 npx
- maps_geo 忽略 city 参数 → 本地按 preferences.amap_city 过滤候选，避免"静安寺→台州"歧义
- 返回结构：bicycling `data.paths[0].duration`（秒）；transit `route.transits[0].duration`（秒）；
  driving `route.paths[0].duration`（秒）；距离取骑行路线 distance（米）
"""
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

from agent.config import get_settings

AMAP_TOOL_TIMEOUT = 60
COMMUTE_TOTAL_TIMEOUT = 600
MCP_CALL_INTERVAL_SEC = 0.45  # 高德 QPS 上限 3 次/秒：串行调用加间隔，避免 CUQPS 限流


class AmapMcpError(RuntimeError):
    """高德 MCP 不可用（未配置/启动失败/调用异常）"""


def _make_client():
    from langchain_mcp_adapters.client import MultiServerMCPClient

    settings = get_settings()
    url = (getattr(settings, "amap_url", "") or "").strip()
    #1.Remote MCP（魔搭托管 streamable HTTP）优先：无需本地 npx，地址自带鉴权
    if url:
        return MultiServerMCPClient({
            "amap": {"transport": "http", "url": url},
        })

    #2.回退本地 npx stdio（AMAP_MAPS_API_KEY 通过环境变量传入）
    key = settings.amap_server_key
    if not key:
        raise AmapMcpError("未配置高德 MCP：请填写 Remote URL 或 AMAP_MAPS_API_KEY")
    return MultiServerMCPClient({
        "amap": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {"AMAP_MAPS_API_KEY": key},
        }
    })


def _extract_text(result) -> str:
    """从 MCP 工具返回中取出正文文本"""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        texts = []
        for block in result:
            if isinstance(block, dict):
                texts.append(block.get("text", ""))
            else:
                texts.append(str(block))
        return "\n".join(t for t in texts if t)
    return str(result)


async def _call(tools_by_name: dict, name: str, args: dict, retries: int = 2) -> dict:
    """调用单个 MCP 工具并解析 JSON（节流 + 限流重试）

    高德各接口 QPS 上限 3 次/秒；MCP 内部无节流，这里串行调用强制间隔，
    遇到 CUQPS 限流时退避重试。
    """
    tool = tools_by_name.get(name)
    if tool is None:
        raise AmapMcpError(f"高德 MCP 未提供 {name} 工具")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        await asyncio.sleep(MCP_CALL_INTERVAL_SEC)  # 节流
        try:
            result = await asyncio.wait_for(tool.ainvoke(args), timeout=AMAP_TOOL_TIMEOUT)
            text = _extract_text(result)
            data = json.loads(text)
            info = str(data.get("info") or data.get("errmsg") or "")
            if "CUQPS" in info.upper() or "QUOTA" in info.upper():
                raise AmapMcpError(f"{name} 高德限流：{info}")
            return data
        except AmapMcpError:
            raise
        except Exception as e:
            last_error = e
            message = str(e).upper()
            if attempt < retries and ("CUQPS" in message or "EXCEEDED" in message or "LIMIT" in message):
                await asyncio.sleep(1.0 + attempt)  # 退避后重试
                continue
            if isinstance(e, json.JSONDecodeError):
                raise AmapMcpError(f"{name} 返回格式异常：{str(e)[:120]}") from e
            raise AmapMcpError(f"{name} 调用失败：{type(e).__name__}: {str(e)[:160]}") from e
    raise AmapMcpError(f"{name} 调用失败：{last_error}")


def _city_core(name: str) -> str:
    """城市名规范化：'上海城区' / '上海市' / '上海' → '上海'（直辖市兼容）"""
    text = str(name or "").strip()
    if text.endswith("城区"):
        text = text[:-2]
    return text.rstrip("市")


def _pick_city_location(geo_data: dict, city: str) -> str | None:
    """从 maps_geo 返回中挑选与配置城市匹配的坐标（city 参数被 MCP 忽略，需本地过滤）"""
    candidates = geo_data.get("return") or []
    if not candidates:
        return None
    if city:
        #1.直辖市的高德区划名为"上海城区"，geocode 返回"上海市"，需按核心名比较
        core = _city_core(city)
        for item in candidates:
            if _city_core(item.get("city")).startswith(core) or _city_core(item.get("province")).startswith(core):
                return item.get("location")
        logger.warning("高德 MCP 地理编码无城市匹配（期望 {}），候选：{}",
                       city, [c.get("city") for c in candidates][:5])
        return None
    return candidates[0].get("location")


def _geo_address(city: str, address: str) -> str:
    """MCP maps_geo 的 city 参数无效：把城市核心名拼进地址以提高定位准确率"""
    core = _city_core(city)
    address_text = str(address)
    if core and core not in address_text:
        return f"{core}{address_text}"
    return address_text


def _seconds_to_min(value) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(1, round(seconds / 60)) if seconds > 0 else None


async def _compute_all(tools_by_name: dict, origin_lnglat: str, city: str,
                       locations: dict[str, str],
                       on_done=None) -> dict[str, dict]:
    """在单个 MCP 会话内批量计算（locations: address -> lnglat）

    Args:
        on_done: 可选同步回调 on_done(address, ok)，每处理完一个地点调用（用于进度上报）
    """
    results: dict[str, dict] = {}
    #1.公共交通接口的城市参数要用核心城市名（"上海城区" 非法）
    transit_city = _city_core(city) or city
    for address, dest in locations.items():
        try:
            data: dict = {}
            bike = await _call(tools_by_name, "maps_bicycling",
                               {"origin": origin_lnglat, "destination": dest})
            path = ((bike.get("data") or {}).get("paths") or [{}])[0]
            if path.get("distance") is not None:
                data["distance_m"] = int(float(path["distance"]))
            data["bike_min"] = _seconds_to_min(path.get("duration"))

            driving = await _call(tools_by_name, "maps_direction_driving",
                                  {"origin": origin_lnglat, "destination": dest})
            dpath = ((driving.get("route") or {}).get("paths") or [{}])[0]
            data["drive_min"] = _seconds_to_min(dpath.get("duration"))

            if city:
                transit = await _call(tools_by_name, "maps_direction_transit_integrated", {
                    "origin": origin_lnglat, "destination": dest,
                    "city": transit_city, "cityd": transit_city,
                })
                transits = ((transit.get("route") or {}).get("transits") or [{}])
                data["transit_min"] = _seconds_to_min(transits[0].get("duration"))

            results[address] = data
            _notify_done(on_done, address, True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("高德 MCP 通勤计算失败 address={!r}：{}", address, type(e).__name__)
            results[address] = {}
            _notify_done(on_done, address, False)
    return results


def _notify_done(on_done, address: str, ok: bool) -> None:
    """进度回调（回调异常不影响计算）"""
    if on_done is None:
        return
    try:
        on_done(address, ok)
    except Exception:
        logger.debug("通勤进度回调异常", exc_info=True)


def _record_failure(failures: list[dict] | None, address: str, reason: str) -> None:
    """记录失败明细（回传设置页展示）"""
    if failures is not None:
        failures.append({"location": address, "reason": reason})


async def _compute_async(rows: list[dict], origin_address: str, city: str,
                         on_done=None, failures: list[dict] | None = None) -> dict[int, dict]:
    """Proactor 循环内执行：启动 MCP 会话并计算全部地点"""
    from langchain_mcp_adapters.tools import load_mcp_tools

    client = _make_client()
    async with client.session("amap") as session:
        tools = await load_mcp_tools(session)
        by_name = {t.name: t for t in tools}

        geo = await _call(by_name, "maps_geo", {"address": _geo_address(city, origin_address), "city": city})
        origin_lnglat = _pick_city_location(geo, city)
        if not origin_lnglat:
            raise AmapMcpError(f"通勤起点无法定位：{origin_address!r}")

        #1.地点去重后批量地理编码（每处只解析一次）
        addresses: dict[str, str] = {}  # address -> lnglat
        for row in rows:
            address = str(row.get("location") or "").strip()
            if not address or address in addresses:
                continue
            try:
                dest_geo = await _call(by_name, "maps_geo", {
                    "address": _geo_address(city, address), "city": city,
                })
            except AmapMcpError as e:
                logger.warning("高德 MCP 地理编码失败（跳过 {!r}）：{}", address, e)
                _record_failure(failures, address, f"地理编码失败：{str(e)[:100]}")
                continue
            dest = _pick_city_location(dest_geo, city)
            if dest:
                addresses[address] = dest
            else:
                logger.info("高德 MCP 无法定位工作地点（跳过）：{!r}", address)
                candidates = dest_geo.get("return") or []
                reason = (
                    "地址不在当前城市（已按城市过滤）" if candidates
                    else "地址无法识别（高德无匹配结果）"
                )
                _record_failure(failures, address, reason)

        computed = await _compute_all(by_name, origin_lnglat, city, addresses, on_done=on_done)
        #3.路线全部失败的地址也计入失败明细
        for address, data in computed.items():
            if not any(value is not None for value in (data or {}).values()):
                _record_failure(failures, address, "路线查询失败（骑行/驾车/公交均无结果）")

    #2.映射回 job id
    result: dict[int, dict] = {}
    for row in rows:
        address = str(row.get("location") or "").strip()
        data = computed.get(address)
        if data:
            result[row["id"]] = data
    return result


def _run_in_proactor_loop(coro):
    """Windows 上 MCP stdio 子进程需要 ProactorEventLoop（独立线程执行）"""
    if sys.platform == "win32" and hasattr(asyncio, "ProactorEventLoop"):
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _find_amap_error(exc: BaseException) -> AmapMcpError | None:
    """从 ExceptionGroup 中递归提取 AmapMcpError（统一对外错误文案）"""
    if isinstance(exc, AmapMcpError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_amap_error(sub)
            if found is not None:
                return found
    return None


async def compute_commute_batch(rows: list[dict], origin_address: str,
                                city: str, on_done=None,
                                failures: list[dict] | None = None) -> dict[int, dict]:
    """批量计算通勤（rows: [{id, location}]）

    Args:
        on_done: 可选同步回调 on_done(address, ok)，每处理完一个地点调用（进度上报）
        failures: 可选列表，收集失败明细 [{location, reason}]

    Returns:
        {job_id: {"distance_m": int|None, "bike_min": int|None,
                  "transit_min": int|None, "drive_min": int|None}}

    Raises:
        AmapMcpError: MCP 未配置/启动失败/会话异常
    """
    if not rows:
        return {}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_in_proactor_loop,
                _compute_async(rows, origin_address, city, on_done, failures),
            ),
            timeout=COMMUTE_TOTAL_TIMEOUT,
        )
    except AmapMcpError:
        raise
    except asyncio.TimeoutError as e:
        raise AmapMcpError(f"高德 MCP 超时（>{COMMUTE_TOTAL_TIMEOUT}s）") from e
    except Exception as e:
        # ExceptionGroup 会隐藏子异常，记录完整堆栈便于定位（npx/会话启动失败等）
        logger.exception("高德 MCP 通勤计算异常（完整堆栈）")
        #2.优先透出内部的 AmapMcpError 文案（如"通勤起点无法定位"），而不是 ExceptionGroup
        found = _find_amap_error(e)
        if found is not None:
            raise AmapMcpError(str(found)) from e
        raise AmapMcpError(f"高德 MCP 调用失败：{type(e).__name__}: {e}") from e