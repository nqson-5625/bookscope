"""SODA3 HTTP client with bounded retries."""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class SodaClient:
    """POST JSON SODA3 client; retry transient failures only."""

    def __init__(self, url: str, token: str, *, attempts: int = 4,
                 timeout: int = 120, backoff: float = 1.0):
        if not url.startswith("https://") or not token:
            raise ValueError("HTTPS source URL and SOCRATA_APP_TOKEN are required")
        if attempts < 1 or timeout < 1 or backoff < 0:
            raise ValueError("invalid client retry settings")
        self.url, self.token = url, token
        self.attempts, self.timeout, self.backoff = attempts, timeout, backoff

    def query(self, query: str, *, page: int | None = None,
              page_size: int | None = None) -> list[dict]:
        body = {"query": query, "includeSystem": True, "includeSynthetic": False}
        if page is not None:
            if page < 1 or page_size is None or not 1 <= page_size <= 50000:
                raise ValueError("invalid SODA3 page")
            body["page"] = {"pageNumber": page, "pageSize": page_size}
        request = Request(self.url, data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json",
                                   "X-App-Token": self.token,
                                   "User-Agent": "BookScope-ingestion/1.0"}, method="POST")
        for attempt in range(self.attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    rows = json.load(response)
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("SODA3 response must be an array of objects")
                return rows
            except HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 == self.attempts:
                    detail = exc.read(1000).decode("utf-8", errors="replace")
                    raise RuntimeError(f"SODA3 HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError) as exc:
                if attempt + 1 == self.attempts:
                    raise RuntimeError(f"SODA3 unavailable: {exc}") from exc
            time.sleep(self.backoff * 2 ** attempt)
        raise AssertionError("unreachable")
