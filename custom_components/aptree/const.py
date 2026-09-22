"""Constants for the APTREE integration."""

from datetime import timedelta

DOMAIN = "aptree"
PLATFORMS = ["select", "sensor"]

API_BASE_URL = "https://aptree.co.kr"
API_REQUEST_TIMEOUT = 20
DEFAULT_COMMUNITY_ID = "6745"
CONF_COMMUNITY_ID = "community_id"

DEFAULT_UPDATE_INTERVAL = timedelta(hours=12)

ATTR_BILLING_MONTH = "billing_month"
ATTR_HISTORY = "history"
ATTR_MONTHLY_DETAILS = "monthly_details"
