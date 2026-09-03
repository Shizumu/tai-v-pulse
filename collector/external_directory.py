from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DIRECTORY_NAME = "Taiwan VTuber Data"
DIRECTORY_SITE_URL = "https://taiwanvtuberdata.github.io/"
DIRECTORY_CSV_URL = (
    "https://raw.githubusercontent.com/TaiwanVtuberData/"
    "TaiwanVtuberTrackingData/master/DATA/TW_VTUBER_TRACK_LIST.csv"
)
DIRECTORY_COMMITS_URL = (
    "https://api.github.com/repos/TaiwanVtuberData/TaiwanVtuberTrackingData/commits"
    "?path=DATA%2FTW_VTUBER_TRACK_LIST.csv&per_page=1"
)
DIRECTORY_LICENSE = "Unlicense"
MAX_DIRECTORY_BYTES = 8 * 1024 * 1024
CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
REQUIRED_COLUMNS = {
    "Display Name",
    "Youtube Channel ID",
    "Graduation Date",
    "Activity",
    "Group Name",
    "Nationality",
}


def parse_directory_csv(text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
        suffix = f"：{', '.join(missing)}" if missing else ""
        raise ValueError(f"外部名錄缺少必要欄位{suffix}")

    section = ""
    row_count = 0
    individual_tw_count = 0
    inactive_count = 0
    missing_youtube_count = 0
    invalid_youtube_count = 0
    duplicate_count = 0
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in reader:
        row_count += 1
        display_name = str(row.get("Display Name") or "").strip()
        row_id = str(row.get("ID") or "").strip()
        marker = row_id if row_id.startswith("##") else display_name
        if marker.startswith("##"):
            section = marker.removeprefix("##").strip().casefold()
            continue
        if section != "taiwanese vtubers":
            continue
        if str(row.get("Nationality") or "").strip().upper() != "TW":
            continue
        individual_tw_count += 1
        activity = str(row.get("Activity") or "").strip()
        graduation_date = str(row.get("Graduation Date") or "").strip()
        if activity.casefold() != "active" or graduation_date:
            inactive_count += 1
            continue
        channel_id = str(row.get("Youtube Channel ID") or "").strip()
        if not channel_id:
            missing_youtube_count += 1
            continue
        if not CHANNEL_ID_PATTERN.fullmatch(channel_id):
            invalid_youtube_count += 1
            continue
        if channel_id in seen:
            duplicate_count += 1
            continue
        seen.add(channel_id)
        entries.append({
            "channel_id": channel_id,
            "display_name": display_name[:300],
            "activity": activity[:40],
            "group_name": str(row.get("Group Name") or "").strip()[:300],
            "nationality": "TW",
        })

    if not entries:
        raise ValueError("外部名錄沒有可驗證的活動中台灣個人 VTuber YouTube 頻道")
    return {
        "entries": entries,
        "row_count": row_count,
        "individual_tw_count": individual_tw_count,
        "selected_count": len(entries),
        "inactive_count": inactive_count,
        "missing_youtube_count": missing_youtube_count,
        "invalid_youtube_count": invalid_youtube_count,
        "duplicate_count": duplicate_count,
    }


def _read_url(url: str, *, accept: str, maximum: int) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "tai-v-pulse-external-directory/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(maximum + 1)
            headers = {key.casefold(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"外部名錄回應 HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"無法連線外部名錄：{error.reason}") from error
    if len(data) > maximum:
        raise ValueError("外部名錄檔案超過安全上限")
    return data, headers


def fetch_directory() -> dict[str, Any]:
    csv_bytes, headers = _read_url(
        DIRECTORY_CSV_URL,
        accept="text/csv,text/plain;q=0.9",
        maximum=MAX_DIRECTORY_BYTES,
    )
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("外部名錄不是有效的 UTF-8 CSV") from error

    commit_sha: str | None = None
    try:
        commit_bytes, _ = _read_url(
            DIRECTORY_COMMITS_URL,
            accept="application/vnd.github+json",
            maximum=512 * 1024,
        )
        payload = json.loads(commit_bytes.decode("utf-8"))
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            value = str(payload[0].get("sha") or "")
            if re.fullmatch(r"[0-9a-f]{40}", value):
                commit_sha = value
    except (RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        # The content hash remains authoritative if GitHub's commit endpoint is unavailable.
        pass

    return {
        "text": text,
        "source_name": DIRECTORY_NAME,
        "source_site_url": DIRECTORY_SITE_URL,
        "source_url": DIRECTORY_CSV_URL,
        "source_commit": commit_sha,
        "source_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "source_etag": headers.get("etag"),
        "license": DIRECTORY_LICENSE,
    }
