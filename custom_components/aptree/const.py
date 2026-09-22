"""Constants for the APTREE integration."""

from datetime import timedelta

DOMAIN = "aptree"
PLATFORMS = ["sensor"]

API_BASE_URL = "https://user-api.aptree.co.kr"
API_APP_PLATFORM = "android"
API_APP_VERSION = "4.3.1"
API_APP_BUILD = "12242051"
API_REQUEST_TIMEOUT = 20

DEFAULT_UPDATE_INTERVAL = timedelta(hours=12)

ATTR_BILLING_MONTH = "billing_month"
ATTR_HISTORY = "history"
