"""时间工具：把「时间」这类高频易错计算从模型手里收回到代码。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from app.core.exceptions import ToolExecutionError
from app.tools.registry import tool

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


@tool(
    name="current_time",
    display_name="当前时间",
    description="获取指定时区的当前日期时间与星期。默认 Asia/Shanghai。",
    category="system",
    timeout_s=3.0,
    idempotent=False,
    tags=["time"],
)
async def current_time(
    timezone: Annotated[str, Field(description="IANA 时区名，例如 Asia/Shanghai")] = "Asia/Shanghai",
) -> dict[str, Any]:
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolExecutionError(f"未知时区: {timezone}") from exc

    now = datetime.now(tz)
    return {
        "timezone": timezone,
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": _WEEKDAYS[now.weekday()],
        "iso": now.isoformat(timespec="seconds"),
    }
