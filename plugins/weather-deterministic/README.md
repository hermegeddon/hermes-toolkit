# weather-deterministic — a Hermes plugin

Answer **"what's the weather"** instantly, with **zero LLM calls and zero MCP**.

This is a Hermes user plugin (`kind: standalone`). It detects weather intent on
inbound messages, answers them from the free [Open-Meteo](https://open-meteo.com)
API entirely inside the gateway/CLI process, and **never creates an AIAgent** for
that turn. Anything that isn't a clean weather request **falls through** to the
normal LLM pipeline untouched.

## What it does

Two deterministic, no-LLM surfaces:

1. **`pre_gateway_dispatch` hook** — fires on every inbound gateway message
   *before* auth/agent dispatch. When the text looks like a weather request and
   is not already a slash command, it rewrites the message to
   `/weather <maybe-location>`. The rewrite is then served by the plugin's own
   `/weather` command — the LLM is never consulted for that turn.
2. **`/weather [location]` slash command** — available in gateway sessions and
   the interactive CLI. Calls Open-Meteo and returns a formatted answer.

A typical answer:

```
Weather for New York, NY
Now: 82°F, Mainly clear
  (feels like 79°F, humidity 28%, wind 14 mph)

3-day outlook:
  2026-06-04: Overcast, 84°/57°F, 1% precip
  2026-06-05: Heavy rain, 74°/66°F, 59% precip
  2026-06-06: Light drizzle, 83°/64°F, 61% precip
```

It reports current temperature, "feels like", **humidity**, wind, and a 3-day
outlook with daily highs/lows and **precipitation probability**. Daily rollovers
use your configured **IANA timezone** so "today" means your local today.

## Why deterministic

`"what's the weather"` is a fully-deterministic question: the answer is decided
entirely by public data (Open-Meteo) and your configured location — no
user-specific context, no disambiguation, verifiable without a model. Handling it
in-process means **0 LLM calls** for the single most common everyday query:
faster, free, and identical every time. On any doubt — a homophone like
`"whether to merge"`, generic chatter, an already-typed slash command — the
matcher returns no match and the message falls through to the LLM. It never
answers non-weather text.

## How it stays safe

- **No API key, no MCP, stdlib-only networking** (`urllib`).
- Every HTTP call is bounded by an explicit timeout (`HTTP_TIMEOUT = 8s`).
- `answer_weather()` **never raises**: any network/JSON failure returns a clear
  "temporarily unavailable" string — the LLM is never used as a fallback.
- Ships **no private/home coordinates** — the only built-in default is a neutral,
  world-famous public landmark (Greenwich / the Prime Meridian) clearly marked
  `EXAMPLE — change me`.

## Install

1. Copy this directory into your Hermes home plugins folder:

   ```
   cp -r weather-deterministic  "$HERMES_HOME/plugins/"
   ```

   (Default `HERMES_HOME` is `~/.hermes`.)

2. Enable it in your `config.yaml` under `plugins.enabled`:

   ```yaml
   plugins:
     enabled:
       - weather-deterministic
   ```

3. Restart the gateway (or start a new CLI session). On load you'll see:

   ```
   weather-deterministic plugin registered (/weather + pre_gateway_dispatch)
   ```

## Config — set your own location

When a message names a city (`"weather in Denver"`) the plugin geocodes it. When
**no** city is named (`"what's the weather"`) it uses your configured **default
location**. Set it in `config.yaml`, as a sibling of `enabled` under `plugins:`:

```yaml
plugins:
  enabled:
    - weather-deterministic
  weather-deterministic:           # <-- block name == plugin name
    default_lat: 40.7128           # your latitude  (decimal degrees)
    default_lon: -74.0060          # your longitude (decimal degrees)
    timezone: America/New_York     # IANA tz name (local day rollovers)
    location_name: "New York, NY"  # label shown in the reply
```

Or via **environment variables** (highest priority — handy for CI / containers):

| Variable | Purpose |
|---|---|
| `WEATHER_DEFAULT_LAT` | Default latitude (decimal degrees) |
| `WEATHER_DEFAULT_LON` | Default longitude (decimal degrees) |
| `WEATHER_DEFAULT_TIMEZONE` | IANA timezone (e.g. `America/Chicago`) |
| `WEATHER_DEFAULT_LOCATION_NAME` | Label shown in the reply |

**Resolution order** (highest priority first): env vars → `config.yaml`
`plugins.weather-deterministic` block → built-in neutral **EXAMPLE** (Greenwich).
The example is there only so a fresh install answers *something* before you
configure your own location — **change it**.

Find your coordinates at e.g. [openstreetmap.org](https://www.openstreetmap.org)
(right-click → "Show address") and your timezone in the
[IANA tz list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

## Files

| File | Role |
|---|---|
| `plugin.yaml` | Manifest + documented default-location config block |
| `__init__.py` | `register()` — wires the `/weather` command + `pre_gateway_dispatch` hook |
| `weather_core.py` | Intent matcher, location parser, config resolution, Open-Meteo fetch, formatter |
| `tests/` | Network-free unit tests (matcher, fall-through, config, formatter) |
| `run_tests.sh` | Test harness (uses the hermes venv python if present) |

## Tests

```
./run_tests.sh
```

11 unit tests, **no network**: the intent matcher (true positives + homophone /
chatter fall-through), location extraction, config-driven default resolution
(env / neutral example), the WMO code map, the formatter (humidity + precip
probability preserved), and the strict "never raises / never calls the LLM"
failure path (Open-Meteo monkeypatched).

## License

See the repository [LICENSE](../../LICENSE).
