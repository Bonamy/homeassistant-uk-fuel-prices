# UK Fuel Prices — Home Assistant Integration

A custom Home Assistant integration that displays the cheapest fuel prices near you using the official [GOV.UK Fuel Finder API](https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email).

Supports multiple fuel types (petrol, diesel, premium, etc.) in a single integration instance, with smart labelling and per-station price tracking over time.

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-blue)
![Version](https://img.shields.io/badge/version-2.0.0-green)

## Features

- **Top 3 cheapest stations** for each selected fuel type within a configurable radius
- **Per-station sensors** for tracking price history at specific stations over time
- **Multiple fuel types** from a single integration (no duplicate instances needed)
- **Smart labels** — shows "Petrol" and "Diesel" by default; only disambiguates (e.g. "Petrol (E10)", "Petrol (E5)") when you select two of the same family
- **Incremental API updates** — first fetch downloads all data, subsequent refreshes only request changes (dramatically faster)
- **Retry logic** with exponential backoff for the (sometimes unreliable) GOV.UK API
- **Cached data fallback** — if an incremental update fails, sensors continue showing last known prices
- **Configurable** radius, location, and fuel types via the options flow (no need to remove and re-add)
- **Optional driving distances** via OpenRouteService Matrix API

## Supported Fuel Types

| Code | Label |
|------|-------|
| E10 | Regular Unleaded (E10) |
| E5 | Super Unleaded (E5) |
| B7_STANDARD | Diesel (B7) |
| B7_PREMIUM | Premium Diesel |
| B10 | Biodiesel (B10) |
| HVO | HVO Diesel |

## Prerequisites

### 1. GOV.UK Fuel Finder API Credentials

You need a **Client ID** and **Client Secret** from the GOV.UK Fuel Finder developer portal.

1. Go to the [GOV.UK Fuel Finder developer portal](https://www.developer.fuel-finder.service.gov.uk/access-latest-fuelprices)
2. Sign in with your **GOV.UK One Login** account (create one if you don't have one)
3. Register for API access
4. You will receive a **Client ID** and **Client Secret** — keep these safe

### 2. OpenRouteService API Key (Optional)

If you want driving distances (instead of straight-line distances), you can optionally get a free API key:

1. Sign up at [openrouteservice.org](https://openrouteservice.org/dev/#/signup)
2. Create a free API token
3. The free tier allows 2,000 requests/day which is more than sufficient

## Installation

### Manual Installation

1. Download or clone this repository
2. Copy the `uk_fuel_prices` folder to your Home Assistant `custom_components` directory:

   ```
   <config>/custom_components/uk_fuel_prices/
   ```

   Your directory structure should look like:

   ```
   custom_components/
   └── uk_fuel_prices/
       ├── __init__.py
       ├── api.py
       ├── config_flow.py
       ├── const.py
       ├── coordinator.py
       ├── manifest.json
       ├── sensor.py
       ├── strings.json
       ├── icon.png
       ├── icon@2x.png
       └── translations/
           └── en.json
   ```

3. Restart Home Assistant

### HACS Installation (Manual Repository)

1. Open HACS in Home Assistant
2. Go to **Integrations** → click the three dots menu → **Custom repositories**
3. Add `https://github.com/Bonamy/homeassistant-uk-fuel-prices` as an **Integration**
4. Search for "UK Fuel Prices" and install
5. Restart Home Assistant

## Configuration

### Adding the Integration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **UK Fuel Prices**
3. Enter your credentials:
   - **Client ID** — from the Fuel Finder developer portal
   - **Client Secret** — from the Fuel Finder developer portal
   - **Latitude / Longitude** — defaults to your HA home location
   - **Search radius** — in miles (default: 10)
   - **Fuel types** — tick the fuel types you want (e.g. E10 + B7_STANDARD for petrol and diesel)
   - **OpenRouteService API key** — optional, for driving distances
4. Click **Submit**

### Changing Settings After Setup

You can change the radius, location, fuel types, and ORS key at any time without removing the integration:

1. Go to **Settings** → **Devices & Services**
2. Find **UK Fuel Prices** → click **Configure**
3. Update your settings and click **Submit**

The integration will reload automatically with the new settings.

## Sensors

### Cheapest Ranking Sensors

For each selected fuel type, 3 sensors are created showing the cheapest stations:

| Sensor Name Example | Value |
|---|---|
| `Petrol #1 — TESCO (3.2 mi)` | 128.9 p/litre |
| `Petrol #2 — ASDA (5.1 mi)` | 129.5 p/litre |
| `Petrol #3 — SHELL (2.8 mi)` | 130.2 p/litre |
| `Diesel #1 — ASDA (5.1 mi)` | 146.9 p/litre |
| `Diesel #2 — TESCO (3.2 mi)` | 147.5 p/litre |
| `Diesel #3 — BP (4.3 mi)` | 148.2 p/litre |

These sensor names update dynamically — the station name and distance change as prices change.

### Per-Station Sensors

A sensor is also created for every station within your radius that has a price for each selected fuel type. These are useful for tracking price history at a specific station:

| Sensor Name Example | Value |
|---|---|
| `BP — Scole SF CONNECT (4.3 mi) — Petrol` | 131.9 p/litre |
| `BP — Scole SF CONNECT (4.3 mi) — Diesel` | 148.9 p/litre |

### Sensor Attributes

All sensors include these attributes:

| Attribute | Example |
|---|---|
| `station_name` | Scole SF CONNECT |
| `brand` | BP |
| `brand_icon` | https://logo.clearbit.com/bp.com |
| `address` | Norwich Road, Scole |
| `postcode` | IP21 4DT |
| `distance_miles` | 4.3 |
| `latitude` | 52.3706 |
| `longitude` | 1.0712 |
| `fuel_type` | Petrol |
| `fuel_type_code` | E10 |
| `last_update` | 2026-02-03T10:30:00Z |
| `driving_distance_miles` | 5.1 *(only with ORS key)* |

## Dashboard Examples

### Simple Entity Cards

Add the ranking sensors directly to your dashboard using the built-in Entities card. The dynamic names include the brand and distance, so no custom cards are needed:

1. Go to your dashboard → **Edit** → **Add Card** → **Entities**
2. Add the 3 petrol sensors and 3 diesel sensors
3. Optionally split into two cards side by side (one for Petrol, one for Diesel)

### Two-Column Layout

For a clean layout with petrol and diesel side by side, use a Horizontal Stack card containing two Entities cards.

## How It Works

### Data Flow

1. **Initial fetch** — Downloads all ~8,500 stations and ~8,500 price records from the GOV.UK API (~34 batches of 500 records each). This takes around 15 minutes due to API rate limits.
2. **Subsequent fetches** (every 2 hours) — Uses the `effective-start-timestamp` API parameter to only download records that changed since the last fetch. Typically 1-2 batches, completing in seconds.
3. **Processing** — Filters stations within your radius, matches prices, sorts by cheapest, and creates/updates sensors.

### Rate Limits

The GOV.UK Fuel Finder API has these rate limits:

| Limit | Value |
|---|---|
| Requests per minute | 30 |
| Concurrent requests | 1 |
| Requests per day | 5,000 |

The integration respects these with 2-second delays between batches and sequential (not parallel) fetching.

### Error Handling

- **Retry with backoff** — Individual API requests retry up to 3 times on 500/502/503/504 errors with exponential backoff (2s, 4s, 8s)
- **Batch resilience** — Failed batches are skipped during the main pass, then retried once at the end with a 5-second delay
- **Cached fallback** — If an incremental update fails entirely, sensors continue showing the last known prices
- **Fast initial retry** — If the first full fetch fails (e.g. API maintenance), retries every 5 minutes instead of waiting the full 2-hour polling interval

## Upgrading from v1.x

Version 2.0 replaces the dual-instance model (one integration per fuel type) with a single instance supporting multiple fuel types. To upgrade:

1. Remove both existing integration instances (e.g. the Petrol and Diesel entries)
2. Add the integration again
3. In the setup form, tick both fuel types (e.g. E10 and B7_STANDARD)

If you have an existing v1 config that you don't remove, it will continue to work with a single fuel type — the legacy `fuel_type` field is automatically migrated to the new `fuel_types` list.

## Troubleshooting

### Integration shows "Failed setup, will retry"

The GOV.UK API is occasionally down for maintenance (typically returns 504 errors). The integration will automatically retry every 5 minutes until it succeeds.

### Sensors show "Unavailable"

This means the initial data fetch hasn't completed yet, or the API was down when it tried. Check the Home Assistant logs filtered for `uk_fuel_prices` for detailed information about what's happening.

### Missing stations or fuel types

Not all stations report all fuel types. The integration only shows stations that have submitted a price for the selected fuel type. Check the logs for stats like "Results for B7_STANDARD: 12 stations with valid prices, 5 no B7_STANDARD price".

### Slow initial load

The first fetch takes around 15 minutes due to downloading ~17,000 records across ~34 API batches with rate limiting. This only happens once — subsequent updates use incremental fetching and complete in seconds.

## License

This project is provided as-is for personal use with Home Assistant.

## Acknowledgements

- [GOV.UK Fuel Finder](https://www.gov.uk/government/collections/fuel-finder) for the fuel price data
- [OpenRouteService](https://openrouteservice.org/) for optional driving distance calculations
- [Clearbit](https://clearbit.com/) for brand logo URLs
