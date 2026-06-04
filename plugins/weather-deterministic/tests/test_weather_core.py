"""Unit tests for the weather-deterministic core (matcher, location parser,
config-driven default resolution, formatter, and strict fall-through).

Run: python -m pytest tests/test_weather_core.py  (from the plugin dir, or via
run_tests.sh which sets the import path). Network-free — the one forecast test
monkeypatches the HTTP layer so no Open-Meteo call is made.
"""

import weather_core as wc


# --- Intent matcher: true positives ----------------------------------------

def test_matches_weather_questions():
    for q in [
        "what's the weather",
        "what is the weather like",
        "weather",
        "is it going to rain tomorrow",
        "will it snow tonight",
        "what's the temperature outside",
        "how hot is it",
        "how cold is it right now",
        "forecast for this weekend",
        "what's the humidity",
        "weather in Paris",
    ]:
        assert wc.is_weather_intent(q) is True, q


# --- Intent matcher: must FALL THROUGH (defers to the LLM) ------------------

def test_false_positives_fall_through():
    # The matcher is keyword-gated: it requires an explicit weather noun/verb,
    # so generic chatter and the "whether" homophone defer to the LLM. (The
    # bare word "forecast" IS treated as a weather term by design — that is the
    # plugin's documented behavior, so it is not asserted as a fall-through.)
    for q in [
        "whether or not to merge the PR",   # homophone, not weather
        "tell me a joke",
        "what time is it",
        "how do I get to the airport",
        "",
        "   ",
    ]:
        assert wc.is_weather_intent(q) is False, q


# --- Location extraction ----------------------------------------------------

def test_extract_explicit_location():
    assert wc.extract_location("weather in Denver") == "Denver"
    assert wc.extract_location("forecast for New York please") == "New York"
    assert wc.extract_location("what's it like near Boston today") == "Boston"


def test_extract_location_none_when_unnamed():
    # No city named -> None -> caller uses the configured default location.
    assert wc.extract_location("what's the weather") is None
    assert wc.extract_location("is it going to rain") is None


# --- Config-driven default location -----------------------------------------

def test_default_location_from_env(monkeypatch):
    # Env vars are the highest-priority override (handy for CI / containers).
    monkeypatch.setenv("WEATHER_DEFAULT_LAT", "40.7128")
    monkeypatch.setenv("WEATHER_DEFAULT_LON", "-74.0060")
    monkeypatch.setenv("WEATHER_DEFAULT_TIMEZONE", "America/New_York")
    monkeypatch.setenv("WEATHER_DEFAULT_LOCATION_NAME", "New York, NY")
    wc.reset_default_location()
    d = wc.get_default_location()
    assert (round(d.lat, 4), round(d.lon, 4)) == (40.7128, -74.006)
    assert d.timezone == "America/New_York"
    assert d.name == "New York, NY"
    wc.reset_default_location()


def test_default_location_neutral_example(monkeypatch):
    # With no env and no config, the neutral Greenwich EXAMPLE is used so a
    # fresh install answers *something* — and it ships NO private coordinate.
    for k in (
        "WEATHER_DEFAULT_LAT", "WEATHER_DEFAULT_LON",
        "WEATHER_DEFAULT_TIMEZONE", "WEATHER_DEFAULT_LOCATION_NAME",
    ):
        monkeypatch.delenv(k, raising=False)
    # Neutralize any config.yaml on this machine so we see the built-in example.
    monkeypatch.setattr(wc, "_load_default_location",
                        lambda: wc.DefaultLocation(
                            wc._EXAMPLE_LAT, wc._EXAMPLE_LON,
                            wc._EXAMPLE_TIMEZONE, wc._EXAMPLE_LOCATION_NAME))
    wc.reset_default_location()
    d = wc.get_default_location()
    assert d.timezone == "Europe/London"
    assert "EXAMPLE" in d.name
    wc.reset_default_location()


def test_coerce_float_safe():
    assert wc._coerce_float("42.3", 0.0) == 42.3
    assert wc._coerce_float(None, 1.0) == 1.0
    assert wc._coerce_float("", 1.0) == 1.0
    assert wc._coerce_float("not-a-number", 9.0) == 9.0


# --- WMO weather-code mapping ----------------------------------------------

def test_wmo_text():
    assert wc._wmo_text(0) == "Clear sky"
    assert wc._wmo_text(95) == "Thunderstorm"
    assert "Code" in wc._wmo_text(12345)  # unknown code degrades gracefully


# --- Formatter: humidity + precip probability + outlook ---------------------

def test_format_weather_includes_humidity_and_precip():
    data = {
        "current": {
            "temperature_2m": 82.0, "apparent_temperature": 79.0,
            "relative_humidity_2m": 28, "wind_speed_10m": 14, "weather_code": 1,
        },
        "daily": {
            "time": ["2026-06-04", "2026-06-05"],
            "temperature_2m_max": [84, 74],
            "temperature_2m_min": [57, 66],
            "weather_code": [3, 65],
            "precipitation_probability_max": [1, 59],
        },
    }
    out = wc.format_weather("New York, NY", data)
    assert "Weather for New York, NY" in out
    assert "82°F" in out
    assert "humidity 28%" in out          # humidity preserved
    assert "59% precip" in out            # precipitation probability preserved
    assert "3-day outlook:" in out
    assert "2026-06-04" in out


# --- End-to-end answer with the network monkeypatched -----------------------

def test_answer_weather_uses_default_when_unnamed(monkeypatch):
    monkeypatch.setattr(wc, "get_default_location",
                        lambda: wc.DefaultLocation(40.7128, -74.0060,
                                                   "America/New_York", "New York, NY"))

    captured = {}

    def fake_fetch(lat, lon, timezone=None):
        captured["lat"], captured["lon"], captured["tz"] = lat, lon, timezone
        return {
            "current": {"temperature_2m": 70, "weather_code": 0,
                        "relative_humidity_2m": 50},
            "daily": {"time": ["2026-06-04"], "temperature_2m_max": [75],
                      "temperature_2m_min": [55], "weather_code": [0],
                      "precipitation_probability_max": [10]},
        }

    monkeypatch.setattr(wc, "fetch_forecast", fake_fetch)
    out = wc.answer_weather("what's the weather")
    assert "Weather for New York, NY" in out
    # Confirms the config-driven default coords + timezone were used.
    assert (round(captured["lat"], 4), round(captured["lon"], 4)) == (40.7128, -74.006)
    assert captured["tz"] == "America/New_York"


def test_answer_weather_never_raises_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(wc, "fetch_forecast", boom)
    monkeypatch.setattr(wc, "get_default_location",
                        lambda: wc.DefaultLocation(0.0, 0.0, "UTC", "X"))
    out = wc.answer_weather("what's the weather")
    # Degrades to a clear message — never an exception, never an LLM call.
    assert "temporarily unavailable" in out.lower()
