"""通过 Open-Meteo 免费 API 获取实时天气和预报。"""

import json
import logging
from urllib.request import urlopen, Request, URLError
from urllib.parse import quote

logger = logging.getLogger(__name__)

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search?count=3&language=zh&name={city}"
_WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,"
    "apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m"
    "&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
    "&timezone=auto&language=zh&forecast_days={days}"
)

_WMO_CODES: dict[int, str] = {
    0: "晴天", 1: "大部晴朗", 2: "多云", 3: "阴天",
    45: "雾", 48: "雾凇", 51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "中阵雨", 82: "大阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷暴", 96: "雷暴+小冰雹", 99: "雷暴+大冰雹",
}


def _fetch_json(url: str) -> dict | None:
    req = Request(url, headers={"User-Agent": "KoishiAI/1.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        logger.warning(f"[weather] request failed: {e}")
        return None


def _resolve_city(city: str) -> dict | None:
    data = _fetch_json(_GEO_URL.format(city=quote(city)))
    if not data or "results" not in data:
        return None
    best = data["results"][0]
    return {
        "name": best.get("name", city),
        "country": best.get("country", ""),
        "lat": best["latitude"],
        "lon": best["longitude"],
    }


def _weather_code_desc(code: int) -> str:
    return _WMO_CODES.get(code, f"未知({code})")


def get_current(city: str = "Beijing") -> dict:
    city_info = _resolve_city(city)
    if not city_info:
        return {"summary": f"未找到城市：{city}"}

    data = _fetch_json(_WEATHER_URL.format(
        lat=city_info["lat"], lon=city_info["lon"], days=3,
    ))
    if not data or "current" not in data:
        return {"summary": f"获取 {city_info['name']} 天气失败"}

    try:
        cur = data["current"]
        temp = cur["temperature_2m"]
        feels = cur["apparent_temperature"]
        humidity = cur["relative_humidity_2m"]
        wind_speed = cur["wind_speed_10m"]
        code = cur["weather_code"]
        desc = _weather_code_desc(code)
    except KeyError as e:
        logger.warning(f"[weather] unexpected API response: missing {e}")
        return {"summary": f"获取 {city_info['name']} 天气数据格式异常"}

    lines = [
        f"{city_info['name']} 当前天气：{desc}，气温 {temp}°C（体感 {feels}°C）",
        f"湿度 {humidity}%，风速 {wind_speed} km/h",
    ]

    daily = data.get("daily", {})
    if daily:
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        if dates:
            lines.append("未来预报：")
            for i, d in enumerate(dates[:3]):
                try:
                    lines.append(
                        f"  {d}  {_weather_code_desc(codes[i]) if i < len(codes) else '?'}"
                        f"，{int(lows[i])}°C ~ {int(highs[i])}°C"
                    )
                except (IndexError, TypeError):
                    break

    return {
        "city": city_info["name"],
        "temperature": temp,
        "feels_like": feels,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "weather": desc,
        "summary": "\n".join(lines),
    }


def get_forecast(city: str = "Beijing", days: int = 3) -> dict:
    city_info = _resolve_city(city)
    if not city_info:
        return {"summary": f"未找到城市：{city}"}

    days = max(1, min(days, 7))
    data = _fetch_json(_WEATHER_URL.format(
        lat=city_info["lat"], lon=city_info["lon"], days=days,
    ))
    if not data:
        return {"summary": f"获取 {city_info['name']} 预报失败"}

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])
    precips = daily.get("precipitation_probability_max", [])

    if not dates:
        return {"summary": f"获取 {city_info['name']} 预报数据为空"}

    lines = [f"{city_info['name']} {len(dates)}日天气预报："]
    for i, d in enumerate(dates):
        try:
            wcode = codes[i] if i < len(codes) else 0
            precip = int(precips[i]) if i < len(precips) and precips[i] is not None else 0
            lines.append(
                f"  {d}  {_weather_code_desc(wcode)}"
                f"，{int(lows[i])}°C ~ {int(highs[i])}°C"
                f"，降水概率 {precip}%"
            )
        except (IndexError, TypeError, ValueError):
            break

    return {"summary": "\n".join(lines)}