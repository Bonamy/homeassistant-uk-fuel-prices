"""Constants for UK Fuel Prices integration."""

DOMAIN = "uk_fuel_prices"

CONF_CLIENT_ID = "api_token_id"
CONF_CLIENT_SECRET = "api_token_value"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS = "radius"
CONF_FUEL_TYPE = "fuel_type"
CONF_ORS_API_KEY = "ors_api_key"

DEFAULT_RADIUS = 10
DEFAULT_FUEL_TYPE = "E10"
DEFAULT_SCAN_INTERVAL = 86400  # 24 hours (once a day)

FUEL_TYPES = {
    "E10": "Regular Unleaded (E10)",
    "E5": "Super Unleaded (E5)",
    "B7_STANDARD": "Diesel (B7)",
    "B7_PREMIUM": "Premium Diesel",
    "B10": "Biodiesel (B10)",
    "HVO": "HVO Diesel",
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
