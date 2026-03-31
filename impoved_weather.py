from typing import Any, Dict, Optional
import sys
import asyncio
import logging

import httpx
from cachetools import TTLCache
from mcp.server.fastmcp import FastMCP

NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-mcp/2.0"
CACHE_TTL_SECONDS = 600  # 10 minutes


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp")
logger.info("Weather MCP server starting...")
mcp = FastMCP("weather")
client = httpx.AsyncClient(
    headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    },
    timeout=httpx.Timeout(30.0),
)
cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=200, ttl=CACHE_TTL_SECONDS)

async def make_nws_request(url: str, retries: int = 3) -> Optional[Dict[str, Any]]:
    if url in cache:
        logger.info(f"Cache hit for {url}")
        return cache[url]

    for attempt in range(retries):
        try:
            logger.info(f"Fetching {url}")
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            cache[url] = data
            return data

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.warning(f"HTTP {status} error for {url}")
            if status in (429, 503) and attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # exponential backoff
                continue
            break

        except httpx.RequestError as e:
            logger.error(f"Request error for {url}: {e}")
            break

    return None

def format_alert(feature: Dict[str, Any]) -> str:
    props = feature.get("properties", {})
    return (
        f"🚨 Event: {props.get('event', 'Unknown')}\n"
        f"📍 Area: {props.get('areaDesc', 'Unknown')}\n"
        f"⚠️ Severity: {props.get('severity', 'Unknown')}\n"
        f"📝 Description: {props.get('description', 'No description')}\n"
        f"📢 Instructions: {props.get('instruction', 'No instructions')}"
    )


def format_forecast_period(period: Dict[str, Any]) -> str:
    return (
        f"🌤️ {period.get('name', 'Unknown')}:\n"
        f"🌡️ Temp: {period.get('temperature', '?')}°{period.get('temperatureUnit', '')}\n"
        f"💨 Wind: {period.get('windSpeed', 'N/A')} {period.get('windDirection', '')}\n"
        f"📖 Forecast: {period.get('shortForecast', 'No forecast available')}"
    )

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get active weather alerts for a US state (e.g. CA, NY)."""

    if len(state) != 2 or not state.isalpha():
        return "Invalid state code. Use two-letter US state code like CA or NY."

    state = state.upper()
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    features = data.get("features") if data else None

    if not features:
        return f"No active alerts found for {state}."

    alerts = [format_alert(feature) for feature in features]
    return "\n\n---\n\n".join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast using latitude and longitude."""

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return "Invalid latitude or longitude values."

    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    forecast_url = points_data.get("properties", {}).get("forecast") if points_data else None
    if not forecast_url:
        return "Forecast data not available for this location."

    forecast_data = await make_nws_request(forecast_url)
    periods = forecast_data.get("properties", {}).get("periods") if forecast_data else None

    if not periods:
        return "Detailed forecast not available."

    forecasts = [format_forecast_period(p) for p in periods[:5]]
    return "\n\n---\n\n".join(forecasts)

async def shutdown():
    logger.info("Shutting down HTTP client...")
    await client.aclose()

def main():
    try:
        mcp.run(transport="stdio")
    except (KeyboardInterrupt, EOFError):
        logger.info("Server shutdown requested.")
    except Exception as e:
        logger.exception("Unexpected server error")
        raise
    finally:
        asyncio.run(shutdown())


if __name__ == "__main__":
    main()
