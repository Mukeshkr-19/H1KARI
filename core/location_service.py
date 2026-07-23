"""Bounded public location and current-weather lookups.

The adapter talks only to fixed Open-Meteo HTTPS endpoints.  It performs no
lookup at import or construction time, persists nothing, and reflects no raw
provider errors.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_RESPONSE_BYTES = 262_144
_MAX_LOCATION_CODEPOINTS = 100
_TIMEOUT_SECONDS = 10
_LOCATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+./-]{0,63}$")


class LocationServiceError(RuntimeError):
    """Content-free public failure for bounded location operations."""

    def __init__(self) -> None:
        super().__init__("location service unavailable")


@dataclass(frozen=True, repr=False)
class ResolvedLocation:
    label: str
    latitude: float
    longitude: float
    timezone_name: Optional[str]

    def __repr__(self) -> str:
        return "ResolvedLocation(<bounded>)"


@dataclass(frozen=True, repr=False)
class CurrentWeather:
    location_label: str
    condition: str
    temperature_c: float
    apparent_temperature_c: float
    relative_humidity: int
    precipitation_mm: float
    wind_speed_kmh: float

    def __repr__(self) -> str:
        return "CurrentWeather(<bounded>)"

    def to_text(self) -> str:
        temperature_f = self.temperature_c * 9 / 5 + 32
        apparent_f = self.apparent_temperature_c * 9 / 5 + 32
        wind_mph = self.wind_speed_kmh * 0.621371
        return (
            f"Current weather in {self.location_label}: {self.condition}, "
            f"{self.temperature_c:.1f}°C ({temperature_f:.1f}°F), "
            f"feels like {self.apparent_temperature_c:.1f}°C ({apparent_f:.1f}°F), "
            f"humidity {self.relative_humidity}%, wind {self.wind_speed_kmh:.1f} km/h "
            f"({wind_mph:.1f} mph), precipitation {self.precipitation_mm:.1f} mm."
        )


def _valid_public_text(value: Any, *, maximum: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > maximum:
        return None
    if any(
        ord(ch) < 32 or ord(ch) == 127 or unicodedata.category(ch) == "Cf"
        for ch in text
    ):
        return None
    return text


def _valid_location_query(value: str) -> Optional[str]:
    return _valid_public_text(value, maximum=_MAX_LOCATION_CODEPOINTS)


def _bounded_json(response: Any) -> dict[str, Any]:
    if getattr(response, "status_code", None) != 200:
        raise LocationServiceError()
    content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
    if "json" not in content_type:
        raise LocationServiceError()
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if not isinstance(chunk, (bytes, bytearray)):
                raise LocationServiceError()
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise LocationServiceError()
        payload = json.loads(bytes(body).decode("utf-8"))
    except LocationServiceError:
        raise
    except Exception:
        raise LocationServiceError() from None
    if not isinstance(payload, dict) or payload.get("error") is True:
        raise LocationServiceError()
    return payload


def _number(value: Any, *, minimum: float, maximum: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        return None
    return result


def _timezone(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not _LOCATION_ID.fullmatch(value):
        return None
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return value


def _condition_for_code(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "conditions unavailable"
    if value == 0:
        return "clear"
    if value in {1, 2}:
        return "partly cloudy"
    if value == 3:
        return "overcast"
    if value in {45, 48}:
        return "foggy"
    if value in {51, 53, 55, 56, 57}:
        return "drizzle"
    if value in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if value in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if value in {95, 96, 99}:
        return "thunderstorms"
    return "mixed conditions"


class LocationService:
    """Resolve public place names and retrieve current weather."""

    def __init__(self, *, get: Callable[..., Any] = requests.get) -> None:
        self._get = get

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = None
        try:
            response = self._get(
                url,
                params=params,
                timeout=_TIMEOUT_SECONDS,
                stream=True,
                allow_redirects=False,
            )
            return _bounded_json(response)
        except LocationServiceError:
            raise
        except Exception:
            raise LocationServiceError() from None
        finally:
            try:
                response.close()
            except Exception:
                pass

    def resolve(self, query: str) -> Optional[ResolvedLocation]:
        location_query = _valid_location_query(query)
        if location_query is None:
            return None
        payload = self._request(
            _GEOCODING_URL,
            {"name": location_query, "count": 5, "language": "en", "format": "json"},
        )
        results = payload.get("results")
        if not isinstance(results, list):
            return None
        for candidate in results[:5]:
            if not isinstance(candidate, dict):
                continue
            name = _valid_public_text(candidate.get("name"), maximum=120)
            country = _valid_public_text(candidate.get("country"), maximum=120)
            latitude = _number(candidate.get("latitude"), minimum=-90, maximum=90)
            longitude = _number(candidate.get("longitude"), minimum=-180, maximum=180)
            if name is None or latitude is None or longitude is None:
                continue
            label = name if not country or name.casefold() == country.casefold() else f"{name}, {country}"
            if len(label) > 200:
                continue
            return ResolvedLocation(
                label=label,
                latitude=latitude,
                longitude=longitude,
                timezone_name=_timezone(candidate.get("timezone")),
            )
        return None

    def _forecast(self, location: ResolvedLocation, *, current: str) -> dict[str, Any]:
        return self._request(
            _FORECAST_URL,
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "current": current,
                "timezone": "auto",
                "forecast_days": 1,
            },
        )

    def resolve_timezone(self, query: str) -> Optional[tuple[str, str]]:
        location = self.resolve(query)
        if location is None:
            return None
        timezone_name = location.timezone_name
        if timezone_name is None:
            payload = self._forecast(location, current="temperature_2m")
            timezone_name = _timezone(payload.get("timezone"))
        if timezone_name is None:
            return None
        return location.label, timezone_name

    def current_weather(self, query: str) -> Optional[CurrentWeather]:
        location = self.resolve(query)
        if location is None:
            return None
        payload = self._forecast(
            location,
            current=(
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m"
            ),
        )
        current = payload.get("current")
        if not isinstance(current, dict):
            raise LocationServiceError()
        temperature = _number(current.get("temperature_2m"), minimum=-150, maximum=80)
        apparent = _number(current.get("apparent_temperature"), minimum=-180, maximum=100)
        humidity_value = _number(current.get("relative_humidity_2m"), minimum=0, maximum=100)
        precipitation = _number(current.get("precipitation"), minimum=0, maximum=5000)
        wind = _number(current.get("wind_speed_10m"), minimum=0, maximum=500)
        if None in {temperature, apparent, humidity_value, precipitation, wind}:
            raise LocationServiceError()
        return CurrentWeather(
            location_label=location.label,
            condition=_condition_for_code(current.get("weather_code")),
            temperature_c=temperature,
            apparent_temperature_c=apparent,
            relative_humidity=int(humidity_value),
            precipitation_mm=precipitation,
            wind_speed_kmh=wind,
        )


def extract_weather_location(text: str) -> Optional[str]:
    """Extract a bounded place phrase from a weather question."""

    raw = (text or "").strip()
    if not re.search(r"\b(?:weather|temperature|forecast|raining|snowing)\b", raw, re.I):
        return None
    if re.search(r"\btemperature\b", raw, re.I) and not re.search(
        r"\b(?:weather|forecast|raining|snowing)\b|"
        r"\btemperature\s+(?:in|for|at)\b|"
        r"^\s*(?:what(?:'s|\s+is)\s+(?:the\s+)?)?temperature\s*\??\s*$",
        raw,
        re.I,
    ):
        return None
    match = re.search(r"\b(?:in|for|at)\s+(?P<place>.+)$", raw, re.I)
    if match:
        place = match.group("place")
    else:
        match = re.search(r"\b(?:weather|forecast)\s+(?P<place>.+)$", raw, re.I)
        if not match:
            return ""
        place = match.group("place")
    place = re.sub(r"[?!.]+$", "", place).strip()
    place = re.sub(
        r"\b(?:please|right\s+now|now|today|man|bro)\s*$",
        "",
        place,
        flags=re.I,
    ).strip()
    return _valid_location_query(place) or ""
