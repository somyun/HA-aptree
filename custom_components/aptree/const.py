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

INTEGRATION_VERSION = "0.9.1"
CONF_BUILDING = "building"
CONF_UNIT = "unit"
CARD_URL = "/aptree_static/aptree-fees-card.js"
PUBLIC_MONTHS = 13
ARCHIVE_MAX_MONTHS = 60

# Prepares the requested month and redirects to the PDF. Requesting the PDF
# directly only works for months that were prepared before.
PDF_URL = "https://pdf2.vnrisa.com/HP_Bill_Api.aspx"
PDF_HOST_SUFFIX = ".vnrisa.com"
PDF_DIRECTORY = "aptree_bills"
# The server renders each PDF on demand, which can take about a minute.
PDF_ATTEMPTS = 2
PDF_RETRY_DELAY = 20
PDF_REQUEST_GAP = 5
PDF_MAX_FAILURES = 3
PDF_MAX_BYTES = 10 * 1024 * 1024
PDF_RETRY_INTERVAL = 20 * 60 * 60
PDF_TIMEOUT = 180

ATTR_BILLING_MONTH = "billing_month"
ATTR_HISTORY = "history"
ATTR_MONTHLY_DETAILS = "monthly_details"
