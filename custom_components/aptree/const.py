"""Constants for the APTREE integration."""

from datetime import timedelta

DOMAIN = "aptree"
PLATFORMS = ["select", "sensor"]

API_BASE_URL = "https://user-api.aptree.co.kr"
API_APP_PLATFORM = "android"
API_APP_VERSION = "4.3.1"
API_APP_BUILD = "12242051"
API_REQUEST_TIMEOUT = 20
API_UPDATE_TIMEOUT = 65
API_WEB_WORKER_TIMEOUT = 35
BACKFILL_START_DELAY = 90
BACKFILL_STEP_DELAY = 30
BACKFILL_MAX_STEPS = 12
DEFAULT_COMMUNITY_ID = "6745"
CONF_COMMUNITY_ID = "community_id"

DEFAULT_UPDATE_INTERVAL = timedelta(hours=24)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "aptree.billing_history"

ATTR_BILLING_MONTH = "billing_month"
ATTR_HISTORY = "history"
ATTR_MONTHLY_DETAILS = "monthly_details"
