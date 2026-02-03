"""Constants for UK Fuel Prices integration."""

from __future__ import annotations

DOMAIN = "uk_fuel_prices"

CONF_CLIENT_ID = "api_token_id"
CONF_CLIENT_SECRET = "api_token_value"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS = "radius"
CONF_FUEL_TYPES = "fuel_types"
CONF_ORS_API_KEY = "ors_api_key"

# Legacy key for migration from single-fuel-type config
CONF_FUEL_TYPE = "fuel_type"

DEFAULT_RADIUS = 10
DEFAULT_FUEL_TYPES = ["E10"]
DEFAULT_SCAN_INTERVAL = 7200  # 2 hours

# All supported fuel type codes and their full descriptions (for config UI)
FUEL_TYPES = {
    "E10": "Regular Unleaded (E10)",
    "E5": "Super Unleaded (E5)",
    "B7_STANDARD": "Diesel (B7)",
    "B7_PREMIUM": "Premium Diesel",
    "B10": "Biodiesel (B10)",
    "HVO": "HVO Diesel",
}

# Fuel family groupings — used to build smart display labels
# family name, short disambiguator shown only when >1 of same family selected
FUEL_FAMILY: dict[str, tuple[str, str]] = {
    "E10": ("Petrol", "E10"),
    "E5": ("Petrol", "E5"),
    "B7_STANDARD": ("Diesel", "B7"),
    "B7_PREMIUM": ("Diesel", "Premium"),
    "B10": ("Diesel", "B10"),
    "HVO": ("Diesel", "HVO"),
}

PRICE_MIN = 100
PRICE_MAX = 180

TOKEN_URL = "https://www.fuel-finder.service.gov.uk/api/v1/oauth/generate_access_token"
STATIONS_URL = "https://www.fuel-finder.service.gov.uk/api/v1/pfs"
PRICES_URL = "https://www.fuel-finder.service.gov.uk/api/v1/pfs/fuel-prices"
ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

BATCH_SIZE = 500

BRAND_DOMAINS = {
    "tesco": "tesco.com",
    "sainsbury's": "sainsburys.co.uk",
    "sainsburys": "sainsburys.co.uk",
    "asda": "asda.com",
    "morrisons": "morrisons.com",
    "shell": "shell.co.uk",
    "bp": "bp.com",
    "esso": "esso.co.uk",
    "texaco": "texaco.com",
    "jet": "jetlocal.co.uk",
    "gulf": "gulfenergy.co.uk",
    "total": "totalenergies.com",
    "totalenergies": "totalenergies.com",
    "murco": "murco.co.uk",
    "harvest": "harvestenergy.com",
    "applegreen": "applegreenstores.com",
    "costco": "costco.co.uk",
}


def fuel_display_labels(selected_codes: list[str]) -> dict[str, str]:
    """Build display labels for selected fuel types with smart disambiguation.

    Returns a mapping of fuel code -> display label, e.g.:
      {"E10": "Petrol", "B7_STANDARD": "Diesel"}
    or if two petrols are selected:
      {"E10": "Petrol (E10)", "E5": "Petrol (E5)", "B7_STANDARD": "Diesel"}
    """
    from collections import Counter

    # Count how many of each family are selected
    family_counts: Counter[str] = Counter()
    for code in selected_codes:
        family, _ = FUEL_FAMILY.get(code, (code, code))
        family_counts[family] += 1

    labels: dict[str, str] = {}
    for code in selected_codes:
        family, short = FUEL_FAMILY.get(code, (code, code))
        if family_counts[family] > 1:
            labels[code] = f"{family} ({short})"
        else:
            labels[code] = family

    return labels
