"""Constants for the APTREE integration."""

from datetime import timedelta

DOMAIN = "aptree"
PLATFORMS = ["select", "sensor"]

API_BASE_URL = "https://user-api.aptree.co.kr"
API_APP_PLATFORM = "android"
API_APP_VERSION = "4.3.1"
API_APP_BUILD = "12242051"
API_REQUEST_TIMEOUT = 20
API_UPDATE_TIMEOUT = 130
API_EXTEND_TIMEOUT = 60
API_WEB_WORKER_TIMEOUT = 35
BACKFILL_START_DELAY = 90
BACKFILL_STEP_DELAY = 30
BACKFILL_MAX_STEPS = 60
DEFAULT_COMMUNITY_ID = "6745"
CONF_COMMUNITY_ID = "community_id"

DEFAULT_UPDATE_INTERVAL = timedelta(hours=24)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "aptree.billing_history"

INTEGRATION_VERSION = "0.9.0"
CONF_BUILDING = "building"
CONF_UNIT = "unit"
CARD_URL = "/aptree_static/aptree-fees-card.js"
PUBLIC_MONTHS = 13
ARCHIVE_MAX_MONTHS = 60

PDF_URL = "https://vpms.vnrisa.com:8443/VPMS/pdf.do"
PDF_DIRECTORY = "aptree_bills"
PDF_LOOKBACK_MONTHS = 3
PDF_MAX_BYTES = 10 * 1024 * 1024
PDF_RETRY_INTERVAL = 20 * 60 * 60
PDF_TIMEOUT = 60

ATTR_BILLING_MONTH = "billing_month"
ATTR_HISTORY = "history"
ATTR_MONTHLY_DETAILS = "monthly_details"
