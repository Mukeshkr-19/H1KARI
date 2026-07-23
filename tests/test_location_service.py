"""Deterministic tests for bounded public location and weather lookups."""

from __future__ import annotations

import json

import pytest

from core.location_service import (
    CurrentWeather,
    LocationService,
    LocationServiceError,
    extract_weather_location,
)


class FakeResponse:
    def __init__(self, payload, *, status=200, content_type="application/json"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def geocode_payload(*, name="Madrid", country="Spain", timezone="Europe/Madrid"):
    result = {
        "name": name,
        "country": country,
        "latitude": 40.4165,
        "longitude": -3.7026,
        "feature_code": "PPLC",
    }
    if timezone is not None:
        result["timezone"] = timezone
    return {"results": [result]}


def forecast_payload():
    return {
        "timezone": "Europe/Madrid",
        "current": {
            "temperature_2m": 28.0,
            "apparent_temperature": 29.5,
            "relative_humidity_2m": 42,
            "precipitation": 0.0,
            "weather_code": 1,
            "wind_speed_10m": 12.0,
        },
    }


def test_resolve_timezone_uses_geocoding_timezone_without_extra_request():
    get = FakeGet([FakeResponse(geocode_payload())])
    service = LocationService(get=get)

    assert service.resolve_timezone("Madrid") == ("Madrid, Spain", "Europe/Madrid")
    assert len(get.calls) == 1
    url, kwargs = get.calls[0]
    assert url == "https://geocoding-api.open-meteo.com/v1/search"
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 10
    assert kwargs["params"]["name"] == "Madrid"


def test_country_without_geocoding_timezone_uses_coordinate_timezone():
    get = FakeGet(
        [
            FakeResponse(geocode_payload(name="Spain", country="Spain", timezone=None)),
            FakeResponse(
                {"timezone": "Europe/Madrid", "current": {"temperature_2m": 25}}
            ),
        ]
    )
    service = LocationService(get=get)

    assert service.resolve_timezone("Spain") == ("Spain", "Europe/Madrid")
    assert len(get.calls) == 2
    assert get.calls[1][1]["params"]["timezone"] == "auto"


def test_current_weather_returns_validated_bounded_snapshot():
    get = FakeGet([FakeResponse(geocode_payload()), FakeResponse(forecast_payload())])
    weather = LocationService(get=get).current_weather("Madrid")

    assert weather is not None
    assert weather.condition == "partly cloudy"
    assert weather.relative_humidity == 42
    text = weather.to_text()
    assert "Madrid, Spain" in text
    assert "28.0°C (82.4°F)" in text
    assert "precipitation 0.0 mm" in text
    assert repr(weather) == "CurrentWeather(<bounded>)"


def test_unknown_location_returns_none_without_forecast_request():
    get = FakeGet([FakeResponse({})])
    assert LocationService(get=get).current_weather("Atlantis") is None
    assert len(get.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({}, status=500),
        FakeResponse({}, content_type="text/html"),
    ],
)
def test_http_failures_are_content_free(response):
    service = LocationService(get=FakeGet([response]))
    with pytest.raises(LocationServiceError, match="^location service unavailable$"):
        service.resolve("Madrid")


def test_oversized_response_is_rejected():
    response = FakeResponse({"padding": "x" * 300_000})
    with pytest.raises(LocationServiceError, match="^location service unavailable$"):
        LocationService(get=FakeGet([response])).resolve("Madrid")


def test_query_bounds_and_controls_prevent_network_calls():
    get = FakeGet([])
    service = LocationService(get=get)
    assert service.resolve("x" * 101) is None
    assert service.resolve("Mad\u200brid") is None
    assert service.resolve("Mad\nrid") is None
    assert get.calls == []


@pytest.mark.parametrize(
    ("text", "previous_was_weather", "previous_location", "expected"),
    [
        ("what is the weather in Buffalo?", False, None, "Buffalo"),
        ("weather for Tirupati right now", False, None, "Tirupati"),
        ("weather Spain", False, None, "Spain"),
        ("what is the weather?", False, None, ""),
        ("explain atmospheric temperature", False, None, None),
        ("in Buffalo?", True, "Tirupati", "Buffalo"),
        ("what about in Madrid?", True, "Buffalo", "Madrid"),
        ("Buffalo", True, "", "Buffalo"),
        ("about waether", True, "Buffalo", "Buffalo"),
        ("thanks", True, "Buffalo", None),
    ],
)
def test_weather_location_extraction(
    text, previous_was_weather, previous_location, expected
):
    assert extract_weather_location(
        text,
        previous_was_weather=previous_was_weather,
        previous_location=previous_location,
    ) == expected


def test_orchestrator_weather_path_uses_injected_service():
    from core.orchestrator import HIKARI_Orchestrator

    class FakeLocationService:
        def current_weather(self, query):
            assert query == "buffalo"
            return CurrentWeather("Buffalo, United States", "clear", 20, 20, 50, 0, 10)

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch._public_location_service = FakeLocationService()
    answer = orch._handle_special_commands("weather in Buffalo")
    assert "Current weather in Buffalo, United States" in answer


def test_orchestrator_weather_followups_keep_exact_live_location_context():
    from core.orchestrator import HIKARI_Orchestrator

    class FakeLocationService:
        def __init__(self):
            self.queries = []

        def current_weather(self, query):
            self.queries.append(query)
            return CurrentWeather(f"{query}, Test", "clear", 20, 20, 50, 0, 10)

    service = FakeLocationService()
    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch._public_location_service = service

    orch._handle_special_commands("weather in Tirupati")
    buffalo = orch._handle_special_commands("in Buffalo?")
    repeated = orch._handle_special_commands("about waether")

    assert service.queries == ["tirupati", "buffalo", "buffalo"]
    assert "buffalo, Test" in buffalo
    assert "buffalo, Test" in repeated


def test_weather_followup_context_is_cleared_by_unrelated_turn():
    from core.orchestrator import HIKARI_Orchestrator

    class FakeLocationService:
        def current_weather(self, query):
            return CurrentWeather(query, "clear", 20, 20, 50, 0, 10)

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    orch._public_location_service = FakeLocationService()

    orch._handle_special_commands("weather in Tirupati")
    assert orch._handle_special_commands("tell me a joke") is None
    assert orch._handle_special_commands("in Buffalo?") is None
