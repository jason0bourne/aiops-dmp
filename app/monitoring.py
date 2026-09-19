"""Read-only Prometheus connectivity checks for the pilot deployment."""
import json
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from .config import settings


def prometheus_status() -> dict:
    """Return health metadata only; this module never mutates a monitored system."""
    if not settings.prometheus_url:
        return {"configured": False, "reachable": False, "message": "PROMETHEUS_URL is not configured"}
    headers = {"Accept": "application/json"}
    if settings.prometheus_bearer_token:
        headers["Authorization"] = f"Bearer {settings.prometheus_bearer_token}"
    try:
        request = Request(f"{settings.prometheus_url}/api/v1/status/buildinfo", headers=headers)
        with urlopen(request, timeout=4) as response:
            payload = json.loads(response.read())
        return {"configured": True, "reachable": payload.get("status") == "success", "data": payload.get("data", {})}
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return {"configured": True, "reachable": False, "message": str(error)}
