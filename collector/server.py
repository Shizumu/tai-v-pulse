from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import locale
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from collector.oauth import GoogleOAuth, OAuthError, REQUIRED_SCOPES, oauth_error_guidance
from collector.public_transfer import (
    MAX_PACKAGE_BYTES,
    export_package as export_public_package,
    import_package as import_public_package,
    preview_package as preview_public_package,
)


ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = ROOT / "work"
WORK_DIR.mkdir(exist_ok=True)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    env_text: str | None = None
    decoding_error: UnicodeDecodeError | None = None
    for encoding in dict.fromkeys(("utf-8-sig", locale.getpreferredencoding(False), "cp950")):
        try:
            env_text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError as error:
            decoding_error = error
    if env_text is None:
        assert decoding_error is not None
        raise decoding_error
    file_values: dict[str, str] = {}
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key.startswith("export "):
            normalized_key = normalized_key.removeprefix("export ").strip()
        if normalized_key:
            file_values[normalized_key] = value.strip().strip('"').strip("'")
    for key, value in file_values.items():
        os.environ.setdefault(key, value)


load_env(ROOT / ".env")


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class Config:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    host = "127.0.0.1"
    port = env_int("TRACKER_API_PORT", 8787, 1024)
    database_path = Path(os.getenv("DATABASE_PATH", str(WORK_DIR / "tai_v_pulse.sqlite3")))
    min_subscribers = env_int("MIN_SUBSCRIBERS", 1000, 1)
    live_poll_seconds = env_int("LIVE_POLL_SECONDS", 60, 30)
    channel_refresh_hours = env_int("CHANNEL_REFRESH_HOURS", 6, 1)
    upload_scan_hours = env_int("UPLOAD_SCAN_HOURS", 4, 1)
    edition = os.getenv("TAI_V_PULSE_EDITION", "public").strip().lower()
    retention_days = env_int("RETENTION_DAYS", 30, 0)
    creator_retention_days = env_int("CREATOR_RETENTION_DAYS", 0, 0)
    discovery_pages_per_term = min(5, env_int("DISCOVERY_PAGES_PER_TERM", 2, 1))
    quota_general_limit = env_int("QUOTA_GENERAL_LIMIT", 10000, 100)
    quota_search_limit = env_int("QUOTA_SEARCH_LIMIT", 100, 1)
    quota_safety_percent = min(50, env_int("QUOTA_SAFETY_PERCENT", 10, 0))


UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
TAIPEI = ZoneInfo("Asia/Taipei")
SEARCH_TERMS = ("台V", "台灣VTuber", "台灣 VTuber", "Taiwan VTuber")
ENHANCED_LIVE_SCAN_HOURS = (0, 1, 8, 12, 15, 18, 19, 20, 21, 22, 23)
ENHANCED_LIVE_SCAN_MINUTE = 5
ENHANCED_LIVE_SCAN_TIMES = tuple(
    f"{hour:02d}:{ENHANCED_LIVE_SCAN_MINUTE:02d}" for hour in ENHANCED_LIVE_SCAN_HOURS
)
HOURLY_LIVE_SCAN_QUOTA_RESERVE = 1_000
MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("台V", re.compile(r"台\s*[Vv](?:[Tt]uber)?(?![A-Za-z])")),
    ("台灣VTuber", re.compile(r"台灣\s*(?:[Vv](?:[Tt]uber)?|虛擬(?:[Yy]ou[Tt]uber|主播))", re.I)),
    ("Taiwan VTuber", re.compile(r"Taiwan(?:ese)?\s+[Vv](?:[Tt]uber)?\b", re.I)),
)

SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "min_subscribers": (1, 10_000_000),
}

SETTING_CHOICES: dict[str, tuple[int, ...]] = {
    "live_poll_seconds": (30, 60, 90, 120, 300, 600, 900, 1_800, 3_600),
    "channel_refresh_hours": (1, 3, 6, 12, 24, 48, 72, 168),
    "upload_scan_hours": (1, 2, 4, 6, 12, 24, 48, 72, 168),
}

PUBLIC_RETENTION_CHOICES = (7, 14, 30)
PERSONAL_RETENTION_CHOICES = (7, 14, 30, 180, 365, 730, 1_095, 1_825, 3_650, 0)
CREATOR_RETENTION_CHOICES = (30, 180, 365, 730, 1_095, 1_825, 3_650, 0)

CHANNEL_CATEGORIES = ("未分類", "個人勢", "企業勢", "團體勢", "其他")
ACTIVITY_STATUSES = ("活動中", "休止中", "疑似已畢業", "已確認畢業", "狀態不明")
ACTIVITY_REVIEW_STATUSES = {"休止中", "疑似已畢業"}
GRADUATION_PATTERN = re.compile(
    r"已(?:正式)?畢業|正式畢業|活動(?:終止|終了|停止|結束)|停止活動|引退|"
    r"不再(?:直播|更新|活動)|(?:has\s+)?graduated|retired\s+vtuber",
    re.I,
)
HIATUS_PATTERN = re.compile(r"活動休止|休止中|暫停活動|暫停直播|無限期休止|on\s+hiatus", re.I)
CREATOR_METRICS = (
    "views", "engaged_views", "watch_time_hours", "average_view_duration_seconds",
    "average_percentage_viewed", "impressions", "impressions_ctr",
    "subscribers_net", "subscribers_gained", "subscribers_lost", "likes",
    "comments", "shares", "unique_viewers", "returning_viewers",
    "estimated_revenue",
)
CREATOR_SUM_METRICS = {
    "views", "engaged_views", "watch_time_hours", "impressions", "subscribers_net",
    "subscribers_gained", "subscribers_lost", "likes", "comments", "shares",
    "unique_viewers", "returning_viewers", "estimated_revenue",
}
CREATOR_AVERAGE_METRICS = {
    "average_view_duration_seconds", "average_percentage_viewed", "impressions_ctr",
}


def parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    elif isinstance(value, str) and value.strip():
        try:
            source = json.loads(value)
        except json.JSONDecodeError:
            source = re.split(r"[,，#\n]", value)
    else:
        source = []
    result: list[str] = []
    for item in source if isinstance(source, list) else []:
        tag = str(item).strip().lstrip("#＃")
        if tag and tag.casefold() not in {existing.casefold() for existing in result}:
            result.append(tag)
    return result


def normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip().casefold()
    return re.sub(r"[\s_()（）%％:：/／.\-]+", "", text)


HEADER_ALIASES: dict[str, str] = {}
for canonical, aliases in {
    "event_date": ("date", "day", "日期", "日"),
    "video_id": ("video", "video id", "content", "內容", "影片id", "影片 ID"),
    "video_title": ("video title", "content title", "title", "影片標題", "內容標題", "標題"),
    "published_at": ("video publish time", "video publish date", "影片發布時間", "影片發布日期"),
    "duration_seconds": ("duration", "video duration", "時間長度", "影片長度"),
    "views": ("views", "view count", "觀看次數", "瀏覽次數"),
    "engaged_views": ("engaged views", "互動觀看次數"),
    "watch_time_hours": ("watch time (hours)", "watch time hours", "觀看時間 (小時)", "觀看時間小時"),
    "average_view_duration_seconds": ("average view duration", "avg view duration", "平均觀看時間", "平均觀看時長"),
    "average_percentage_viewed": (
        "average percentage viewed", "avg percentage viewed", "平均觀看百分比", "平均觀看比例 (%)",
    ),
    "impressions": ("impressions", "曝光次數"),
    "impressions_ctr": ("impressions click-through rate", "impressions ctr", "曝光點閱率", "曝光點擊率"),
    "subscribers_net": ("subscribers", "net subscribers", "訂閱人數", "訂閱者"),
    "subscribers_gained": (
        "subscribers gained", "gained subscribers", "獲得的訂閱者", "獲得的訂閱人數", "新增訂閱人數",
    ),
    "subscribers_lost": (
        "subscribers lost", "lost subscribers", "流失的訂閱者", "流失的訂閱人數", "取消訂閱人數",
    ),
    "likes": ("likes", "喜歡次數", "按讚數"),
    "comments": ("comments", "留言", "留言數", "已新增留言"),
    "shares": ("shares", "分享", "分享次數"),
    "unique_viewers": ("unique viewers", "不重複觀眾人數", "非重複觀眾人數", "獨立觀眾"),
    "returning_viewers": ("returning viewers", "回訪觀眾", "回訪的觀眾", "回訪觀眾人數"),
    "estimated_revenue": (
        "estimated revenue", "your estimated revenue", "預估收益", "預估收益 (TWD)", "預估營利",
    ),
}.items():
    for alias in aliases:
        HEADER_ALIASES[normalize_header(alias)] = canonical


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text or text in {"—", "-", "--", "N/A", "n/a"}:
        return None
    multiplier = 1.0
    if text[-1:].casefold() == "k":
        multiplier, text = 1_000.0, text[:-1]
    elif text[-1:].casefold() == "m":
        multiplier, text = 1_000_000.0, text[:-1]
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def parse_average_duration(value: Any) -> float | None:
    text = str(value or "").strip()
    if ":" not in text:
        return parse_number(value)
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def normalize_report_date(value: Any) -> str | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text or text.casefold() in {"total", "總計", "合計"}:
        return None
    for format_string in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%d/%m/%Y",
        "%b %d, %Y", "%B %d, %Y",
    ):
        try:
            return datetime.strptime(text, format_string).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:40]


def decode_text_file(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "big5"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def parse_studio_csv(text: str, report_name: str) -> dict[str, Any]:
    lines = [line for line in text.replace("\x00", "").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{report_name} 是空白檔案")

    selected: tuple[list[str], list[dict[str, str]], dict[str, str]] | None = None
    for start in range(min(10, len(lines))):
        sample = "\n".join(lines[start : start + 8])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t" if "\t" in lines[start] else ","
        reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter)
        headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
        mapping = {
            header: HEADER_ALIASES.get(normalize_header(header), "") for header in headers
        }
        if any(value in CREATOR_METRICS for value in mapping.values()):
            selected = (headers, [dict(row) for row in reader], mapping)
            break
    if not selected:
        raise ValueError(f"{report_name} 找不到可辨識的 YouTube Analytics 指標欄位")

    headers, raw_rows, mapping = selected
    recognized_metrics = sorted({value for value in mapping.values() if value in CREATOR_METRICS})
    recognized_dimensions = sorted({
        value for value in mapping.values()
        if value in {"event_date", "video_id", "video_title", "published_at", "duration_seconds"}
    })
    ignored_headers = [header for header in headers if not mapping.get(header)]
    parsed_rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        dimensions: dict[str, str] = {}
        metrics: dict[str, float] = {}
        event_date: str | None = None
        published_at: str | None = None
        duration_seconds: float | None = None
        video_id: str | None = None
        video_title: str | None = None
        row_kind = "detail"
        for header in headers:
            value = str(raw.get(header) or "").strip()
            canonical = mapping.get(header, "")
            if value.casefold() in {"total", "總計", "合計"}:
                row_kind = "total"
            if canonical == "event_date":
                event_date = normalize_report_date(value)
            elif canonical == "video_id":
                video_id = value[:100] or None
            elif canonical == "video_title":
                video_title = value[:500] or None
            elif canonical == "published_at":
                published_at = normalize_report_date(value)
            elif canonical == "duration_seconds":
                duration_seconds = parse_average_duration(value)
            elif canonical in CREATOR_METRICS:
                numeric = parse_average_duration(value) if canonical == "average_view_duration_seconds" else parse_number(value)
                if numeric is not None:
                    metrics[canonical] = numeric
            elif value:
                dimensions[header[:120]] = value[:500]
        if not metrics:
            continue
        if row_kind == "total":
            video_id = None
            video_title = None
            published_at = None
            duration_seconds = None
        identity = {
            "date": event_date,
            "published_at": published_at,
            "duration_seconds": duration_seconds,
            "video_id": video_id,
            "video_title": None if video_id else video_title,
            "row_kind": row_kind,
            "dimensions": dimensions,
        }
        natural_key = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        row_hash = hashlib.sha256(
            json.dumps({"identity": identity, "metrics": metrics}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        parsed_rows.append({
            "report_name": report_name[:240],
            "event_date": event_date,
            "published_at": published_at,
            "duration_seconds": duration_seconds,
            "video_id": video_id,
            "video_title": video_title,
            "row_kind": row_kind,
            "dimensions": dimensions,
            "metrics": metrics,
            "natural_key": natural_key,
            "row_hash": row_hash,
        })
    if not parsed_rows:
        raise ValueError(f"{report_name} 沒有可匯入的數值資料")
    dates = [row["event_date"] for row in parsed_rows if row["event_date"]]
    return {
        "report_name": report_name,
        "headers": headers,
        "recognized_metrics": recognized_metrics,
        "recognized_dimensions": recognized_dimensions,
        "ignored_headers": ignored_headers,
        "rows": parsed_rows,
        "date_start": min(dates) if dates else None,
        "date_end": max(dates) if dates else None,
    }


def decode_creator_upload(payload: dict[str, Any]) -> tuple[bytes, list[dict[str, Any]]]:
    filename = str(payload.get("filename", "")).strip()[:240]
    if not filename:
        raise ValueError("缺少檔名")
    if payload.get("content_base64"):
        try:
            raw = base64.b64decode(str(payload["content_base64"]), validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("上傳內容不是有效檔案") from error
    else:
        raw = str(payload.get("content", "")).encode("utf-8")
    if not raw:
        raise ValueError("上傳檔案是空的")
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("單次上傳上限為 8 MB")

    reports: list[dict[str, Any]] = []
    if filename.casefold().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir() and item.filename.casefold().endswith((".csv", ".tsv"))]
                if not members:
                    raise ValueError("ZIP 裡找不到 CSV 或 TSV 報表")
                if len(members) > 20 or sum(item.file_size for item in members) > 20 * 1024 * 1024:
                    raise ValueError("ZIP 內容過多，請分批上傳")
                for item in members:
                    if item.flag_bits & 0x1 or item.file_size > 4 * 1024 * 1024:
                        raise ValueError("ZIP 含加密或過大的報表")
                    reports.append(parse_studio_csv(decode_text_file(archive.read(item)), item.filename))
        except zipfile.BadZipFile as error:
            raise ValueError("ZIP 檔案已損壞或格式不正確") from error
    elif filename.casefold().endswith((".csv", ".tsv", ".txt")):
        reports.append(parse_studio_csv(decode_text_file(raw), filename))
    else:
        raise ValueError("目前支援 CSV、TSV 或包含這些報表的 ZIP")
    return raw, reports


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def hourly_live_scan_slot(moment: datetime | None = None) -> str | None:
    current = moment or datetime.now(TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TAIPEI)
    else:
        current = current.astimezone(TAIPEI)
    if current.hour not in ENHANCED_LIVE_SCAN_HOURS or current.minute != ENHANCED_LIVE_SCAN_MINUTE:
        return None
    return current.strftime("%Y-%m-%dT%H:%M")


def chunks(values: list[str], size: int = 50) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def parse_duration(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def evidence_for_terms(item: dict[str, Any], terms: Iterable[str]) -> tuple[str, str, str] | None:
    snippet = item.get("snippet", {})
    branding = item.get("brandingSettings", {}).get("channel", {})
    fields = (
        ("頻道名稱", snippet.get("title", "")),
        ("頻道說明", snippet.get("description", "")),
        ("頻道關鍵字", branding.get("keywords", "")),
    )
    for field, text in fields:
        for raw_term in terms:
            label = str(raw_term).strip()
            if not label:
                continue
            normalized_label = re.sub(
                r"\s+", "", unicodedata.normalize("NFKC", label).casefold()
            )
            if normalized_label in {"台v", "台vtuber"}:
                pattern = MATCHERS[0][1]
            elif normalized_label in {"台灣v", "台灣vtuber", "台灣虛擬主播", "台灣虛擬youtuber"}:
                pattern = MATCHERS[1][1]
            elif normalized_label in {"taiwanv", "taiwanvtuber", "taiwanesev", "taiwanesevtuber"}:
                pattern = MATCHERS[2][1]
            else:
                escaped = re.escape(label).replace(r"\ ", r"\s*")
                pattern = re.compile(escaped, re.I)
            match = pattern.search(text or "")
            if match:
                start = max(0, match.start() - 42)
                end = min(len(text), match.end() + 72)
                excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
                return label, field, excerpt
    return None


def evidence_for(item: dict[str, Any]) -> tuple[str, str, str] | None:
    return evidence_for_terms(item, SEARCH_TERMS)


def channel_query_target(query: str) -> tuple[str, str]:
    value = query.strip()
    channel_match = re.search(r"(?:youtube\.com/channel/)?(UC[0-9A-Za-z_-]{20,24})", value, re.I)
    if channel_match:
        return "id", channel_match.group(1)
    handle_match = re.search(r"(?:youtube\.com/)?@([0-9A-Za-z._-]+)", value, re.I)
    if handle_match:
        return "handle", f"@{handle_match.group(1)}"
    if value.startswith("@") and len(value) > 1:
        return "handle", value
    return "search", value


CONTENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "紀念／重大活動": re.compile(r"生日|周年|週年|紀念|新衣|新裝|3D|初配信|初直播|debut", re.I),
    "ASMR": re.compile(r"\bASMR\b|耳かき|助眠|掏耳", re.I),
    "音樂作品": re.compile(r"翻唱|原創曲|原创曲|original\s*song|歌ってみた|cover|music\s*video|\bMV\b", re.I),
    "歌回": re.compile(r"歌回|歌枠|歌唱|唱歌|karaoke|singing", re.I),
    "聯動": re.compile(
        r"【\s*(?:聯動|联动)\s*】|(?:聯動|联动).{0,36}(?:@|ft\.?|feat\.?|"
        r"(?:和|與|跟|同).{1,24}一起|Vtuber|主播|朋朋)|コラボ|collab(?:oration)?|(?:^|[\s【\[(])ft\.?\s|"
        r"(?:^|[\s【\[(])feat\.?\s|\bwith\s+@|[×✕]\s*@?|"
        r"(?:^|[\s【\[(])合作(?:直播|配信|企劃|企划|挑戰|挑战|遊戲|游戏|[\s】\])：:])",
        re.I,
    ),
    "雜談": re.compile(r"雜談|杂谈|聊天|閒聊|闲聊|zatsudan|雑談|free\s*talk", re.I),
    "遊戲": re.compile(r"遊戲|游戏|實況|实况|gameplay|gaming|プレイ", re.I),
}
MIXED_SONG_CHAT_PATTERN = re.compile(
    r"歌\s*[雜杂聊]|[雜杂]\s*歌|歌回.{0,10}(?:雜談|杂谈|聊天|閒聊|闲聊)|"
    r"(?:雜談|杂谈|聊天|閒聊|闲聊).{0,10}歌回",
    re.I,
)
MORNING_CHAT_PATTERN = re.compile(r"早安台|朝活|早安配信|おはよう配信|おはよう(?:雑談|直播)", re.I)
CONTENT_TOPIC_ORDER = ("紀念／重大活動", "ASMR", "歌回", "音樂作品", "雜談", "遊戲", "其他")
CONTENT_ATTRIBUTES = {"聯動"}
SYSTEM_GAME_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Minecraft", ("minecraft", "麥塊", "當個創世神")),
    ("Palworld", ("palworld", "幻獸帕魯", "幻兽帕鲁")),
    ("Apex Legends", ("apex legends", "apex英雄", "apex")),
    ("VALORANT", ("valorant", "瓦羅蘭特", "无畏契约", "無畏契約")),
    ("英雄聯盟", ("league of legends", "英雄聯盟", "英雄联盟", "lol")),
    ("原神", ("genshin impact", "genshin", "原神")),
    ("崩壞：星穹鐵道", ("honkai star rail", "星穹鐵道", "星穹铁道", "星鐵", "星铁")),
    ("鳴潮", ("wuthering waves", "鳴潮", "鸣潮")),
    ("絕區零", ("zenless zone zero", "絕區零", "绝区零", "zzz")),
    ("Grand Theft Auto V", ("grand theft auto v", "gta v", "gta5", "gta 5")),
    ("魔物獵人", ("monster hunter", "魔物獵人", "魔物猎人")),
    ("寶可夢", ("pokemon", "pokémon", "寶可夢", "宝可梦")),
    ("雀魂", ("mahjong soul", "雀魂")),
    ("Splatoon", ("splatoon", "斯普拉遁")),
    ("Dead by Daylight", ("dead by daylight", "黎明死線", "黎明死线", "dbd")),
    ("Among Us", ("among us", "太空狼人殺", "太空狼人杀")),
    ("Phasmophobia", ("phasmophobia", "恐鬼症")),
    ("Fortnite", ("fortnite", "要塞英雄", "堡垒之夜")),
    ("Overwatch", ("overwatch", "鬥陣特攻", "守望先锋")),
    ("Warframe", ("warframe", "戰甲神兵", "星際戰甲")),
    ("Roblox", ("roblox", "機器磚塊", "機器方塊")),
)
CONTENT_TYPE_DESCRIPTIONS = {
    "紀念／重大活動": "生日、周年、新衣、新裝、3D 或初配信等重要節點",
    "ASMR": "助眠、掏耳與近距離聲音內容",
    "音樂作品": "正式上傳的翻唱、原創歌曲或音樂影片",
    "歌回": "以直播形式進行的唱歌或 Karaoke",
    "聯動": "兩個以上創作者共同參與的合作內容",
    "雜談": "聊天、閒聊或自由談話",
    "遊戲": "遊戲實況、遊玩或相關內容",
    "其他": "目前規則尚未辨識的內容",
}

KEYWORD_STOPWORDS = {
    "vtuber", "台v", "直播", "實況", "游戏", "遊戲", "live", "stream",
    "shorts", "short", "精華", "剪輯", "中文", "台灣", "taiwan", "合作",
}


def _contains_game_alias(text: str, alias: str) -> bool:
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    normalized_alias = unicodedata.normalize("NFKC", alias).casefold().strip()
    if not normalized_alias:
        return False
    if re.fullmatch(r"[a-z0-9 .:+_-]+", normalized_alias):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])", normalized_text))
    return normalized_alias in normalized_text


def detect_game_name(title: str, confirmed_game_names: Iterable[str] = ()) -> tuple[str, str] | None:
    for game_name in confirmed_game_names:
        candidate = str(game_name).strip()
        if candidate and _contains_game_alias(title, candidate):
            return candidate, candidate
    for game_name, aliases in SYSTEM_GAME_ALIASES:
        for alias in aliases:
            if _contains_game_alias(title, alias):
                return game_name, alias
    return None


def classify_content_details(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
    live_state: str | None = None,
    confirmed_game_names: Iterable[str] = (),
) -> dict[str, Any]:
    tag_values = [str(tag) for tag in tags]
    confirmed_names = [str(value).strip() for value in confirmed_game_names if str(value).strip()]
    tag_text = " ".join(tag_values)
    description_excerpt = description[:600]
    live_format = live_state in {"live", "upcoming", "completed"}
    topics: list[str] = []
    attributes: list[str] = []
    source = "未辨識"
    evidence = "沒有足夠的標題、YouTube 類別、標籤或說明證據"
    game_name = ""

    def add_topic(label: str) -> None:
        if label not in topics:
            topics.append(label)

    # Title evidence is authoritative. YouTube categories and tags are supporting
    # evidence only, while reusable descriptions are the final fallback. This keeps
    # old SEO tags from overriding an explicit current title such as "早安台".
    event_signal = CONTENT_PATTERNS["紀念／重大活動"].search(title)
    asmr_signal = CONTENT_PATTERNS["ASMR"].search(title)
    song_signal = CONTENT_PATTERNS["歌回"].search(title)
    music_signal = CONTENT_PATTERNS["音樂作品"].search(title)
    chat_signal = CONTENT_PATTERNS["雜談"].search(title)
    game_signal = CONTENT_PATTERNS["遊戲"].search(title)
    morning_signal = MORNING_CHAT_PATTERN.search(title)
    mixed_song_chat = MIXED_SONG_CHAT_PATTERN.search(title)
    detected_game = detect_game_name(title, confirmed_names)

    if mixed_song_chat:
        add_topic("歌回")
        add_topic("雜談")
        source = "標題"
        evidence = f"標題含混合主題「{mixed_song_chat.group(0)}」"
    elif event_signal:
        add_topic("紀念／重大活動")
        source = "標題"
        evidence = f"標題含「{event_signal.group(0)}」"
        if live_format and (song_signal or music_signal):
            add_topic("歌回")
        elif not live_format and music_signal:
            add_topic("音樂作品")
    elif asmr_signal:
        add_topic("ASMR")
        source = "標題"
        evidence = f"標題含「{asmr_signal.group(0)}」"
    elif live_format and (song_signal or music_signal):
        add_topic("歌回")
        source = "標題"
        evidence = f"標題含「{(song_signal or music_signal).group(0)}」"
    elif not live_format and music_signal:
        add_topic("音樂作品")
        source = "標題"
        evidence = f"標題含「{music_signal.group(0)}」"
    elif song_signal:
        add_topic("歌回")
        source = "標題"
        evidence = f"標題含「{song_signal.group(0)}」"
    elif detected_game or game_signal:
        add_topic("遊戲")
        source = "標題（既有確認遊戲）" if detected_game and detected_game[0] in set(confirmed_names) \
            else "標題（系統遊戲別名）" if detected_game else "標題"
        if detected_game:
            game_name = detected_game[0]
            evidence = f"標題含遊戲別名「{detected_game[1]}」"
        else:
            evidence = f"標題含「{game_signal.group(0)}」"
    elif chat_signal or morning_signal:
        signal = chat_signal or morning_signal
        add_topic("雜談")
        source = "標題"
        evidence = f"標題含「{signal.group(0)}」"

    # Do not use collaboration wording from descriptions: phrases such as
    # "除非合作請勿提及其他頻道" and business contact boilerplate are common.
    if not topics and str(category_id or "") == "20":
        add_topic("遊戲")
        source = "YouTube 類別"
        evidence = "YouTube categoryId 20（Gaming）"

    if not topics:
        for label in ("ASMR", "歌回" if live_format else "音樂作品", "雜談", "遊戲"):
            if match := CONTENT_PATTERNS[label].search(tag_text):
                add_topic(label)
                source = "影片標籤"
                evidence = f"影片標籤含「{match.group(0)}」"
                break

    if not topics:
        for label in ("紀念／重大活動", "ASMR", "歌回" if live_format else "音樂作品", "雜談", "遊戲"):
            if match := CONTENT_PATTERNS[label].search(description_excerpt):
                add_topic(label)
                source = "說明文字"
                evidence = f"說明文字含「{match.group(0)}」"
                break

    if not topics:
        add_topic("其他")
    collaboration_tags = {"聯動", "联动", "コラボ", "collab", "collaboration"}
    if CONTENT_PATTERNS["聯動"].search(title) or any(
        tag.strip().lstrip("#＃").lower() in collaboration_tags for tag in tag_values
    ):
        attributes.append("聯動")
    ordered_topics = [label for label in CONTENT_TOPIC_ORDER if label in topics]
    labels = [*ordered_topics, *attributes]
    return {
        "content_type": " + ".join(ordered_topics),
        "topics": ordered_topics,
        "attributes": attributes,
        "labels": labels,
        "classification_source": source,
        "classification_evidence": evidence,
        "game_name": game_name,
    }


def classify_content_labels(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
    live_state: str | None = None,
    confirmed_game_names: Iterable[str] = (),
) -> list[str]:
    return classify_content_details(
        title, description, tags, category_id, live_state, confirmed_game_names
    )["labels"]


def classify_content_fields(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
    live_state: str | None = None,
    confirmed_game_names: Iterable[str] = (),
) -> str:
    return classify_content_details(
        title, description, tags, category_id, live_state, confirmed_game_names
    )["content_type"]


def video_format(live_state: str, duration_seconds: int | None, title: str = "", description: str = "") -> str:
    if live_state in {"live", "upcoming", "completed"}:
        return "直播"
    text = f"{title} {description}"
    if re.search(r"[#＃]\s*shorts?\b", text, re.I) or (duration_seconds is not None and duration_seconds <= 60):
        return "Shorts"
    return "影片"


def percentile(values: Iterable[float | int | None], fraction: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * fraction
    lower = int(position)
    upper = min(len(clean) - 1, lower + 1)
    weight = position - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def parse_api_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_video_keywords(title: str, raw_tags: str | None) -> list[str]:
    terms: list[str] = []
    if raw_tags:
        try:
            tags = json.loads(raw_tags)
            if isinstance(tags, list):
                terms.extend(str(tag).strip() for tag in tags)
        except json.JSONDecodeError:
            pass
    terms.extend(match.group(1).strip() for match in re.finditer(r"[#＃]([\w\u3400-\u9fff]{2,30})", title))
    return [
        term for term in terms
        if 2 <= len(term) <= 30 and term.casefold() not in KEYWORD_STOPWORDS
    ]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                  channel_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  handle TEXT,
                  description TEXT,
                  keywords TEXT,
                  country TEXT,
                  thumbnail_url TEXT,
                  subscriber_count INTEGER,
                  view_count INTEGER,
                  video_count INTEGER,
                  hidden_subscriber_count INTEGER NOT NULL DEFAULT 0,
                  uploads_playlist_id TEXT,
                  category TEXT NOT NULL DEFAULT '未分類',
                  organization_name TEXT NOT NULL DEFAULT '',
                  manual_tags TEXT NOT NULL DEFAULT '[]',
                  activity_status TEXT NOT NULL DEFAULT '活動中',
                  activity_status_source TEXT NOT NULL DEFAULT 'automatic',
                  activity_status_confidence TEXT NOT NULL DEFAULT 'normal',
                  activity_status_reason TEXT NOT NULL DEFAULT '',
                  activity_status_detected_at TEXT,
                  activity_status_reviewed_at TEXT,
                  activity_status_manual_lock INTEGER NOT NULL DEFAULT 0,
                  last_activity_at TEXT,
                  discovery_status TEXT NOT NULL DEFAULT 'review',
                  match_term TEXT,
                  match_field TEXT,
                  match_excerpt TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_stats_at TEXT,
                  last_upload_scan_at TEXT
                );
                CREATE INDEX IF NOT EXISTS channels_status_idx ON channels(discovery_status);
                CREATE INDEX IF NOT EXISTS channels_subscribers_idx ON channels(subscriber_count DESC);

                CREATE TABLE IF NOT EXISTS excluded_channels (
                  channel_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  reason TEXT NOT NULL DEFAULT 'manual',
                  excluded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_batches (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  started_at TEXT NOT NULL,
                  completed_at TEXT,
                  status TEXT NOT NULL DEFAULT 'running',
                  discovery_terms_json TEXT NOT NULL DEFAULT '[]',
                  pages_per_term INTEGER NOT NULL DEFAULT 0,
                  candidate_count INTEGER NOT NULL DEFAULT 0,
                  examined_count INTEGER NOT NULL DEFAULT 0,
                  eligible_count INTEGER NOT NULL DEFAULT 0,
                  new_count INTEGER NOT NULL DEFAULT 0,
                  refreshed_count INTEGER NOT NULL DEFAULT 0,
                  below_threshold_count INTEGER NOT NULL DEFAULT 0,
                  review_count INTEGER NOT NULL DEFAULT 0,
                  excluded_count INTEGER NOT NULL DEFAULT 0,
                  rejected_count INTEGER NOT NULL DEFAULT 0,
                  error TEXT
                );
                CREATE INDEX IF NOT EXISTS discovery_batches_started_idx
                  ON discovery_batches(started_at DESC);

                CREATE TABLE IF NOT EXISTS discovery_candidates (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_id INTEGER NOT NULL REFERENCES discovery_batches(id) ON DELETE CASCADE,
                  channel_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  handle TEXT,
                  description TEXT NOT NULL DEFAULT '',
                  keywords TEXT NOT NULL DEFAULT '',
                  country TEXT,
                  thumbnail_url TEXT,
                  subscriber_count INTEGER,
                  view_count INTEGER,
                  video_count INTEGER,
                  hidden_subscriber_count INTEGER NOT NULL DEFAULT 0,
                  uploads_playlist_id TEXT,
                  search_terms_json TEXT NOT NULL DEFAULT '[]',
                  match_term TEXT,
                  match_field TEXT,
                  match_excerpt TEXT,
                  validation_status TEXT NOT NULL DEFAULT 'pending',
                  unlisted_reason TEXT NOT NULL DEFAULT '等待規則驗證',
                  handling_status TEXT NOT NULL DEFAULT 'pending',
                  handling_note TEXT NOT NULL DEFAULT '',
                  discovered_at TEXT NOT NULL,
                  handled_at TEXT,
                  UNIQUE(batch_id, channel_id)
                );
                CREATE INDEX IF NOT EXISTS discovery_candidates_batch_idx
                  ON discovery_candidates(batch_id, discovered_at DESC);
                CREATE INDEX IF NOT EXISTS discovery_candidates_channel_idx
                  ON discovery_candidates(channel_id, discovered_at DESC);
                CREATE INDEX IF NOT EXISTS discovery_candidates_status_idx
                  ON discovery_candidates(validation_status, handling_status);

                CREATE TABLE IF NOT EXISTS channel_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  captured_at TEXT NOT NULL,
                  subscriber_count INTEGER,
                  view_count INTEGER,
                  video_count INTEGER
                );
                CREATE INDEX IF NOT EXISTS channel_snapshots_lookup_idx
                  ON channel_snapshots(channel_id, captured_at);

                CREATE TABLE IF NOT EXISTS videos (
                  video_id TEXT PRIMARY KEY,
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  title TEXT NOT NULL,
                  description TEXT,
                  thumbnail_url TEXT,
                  published_at TEXT,
                  duration_seconds INTEGER,
                  category_id TEXT,
                  tags TEXT,
                  content_type TEXT NOT NULL DEFAULT '其他',
                  content_tags TEXT NOT NULL DEFAULT '[]',
                  classification_source TEXT NOT NULL DEFAULT '未辨識',
                  classification_evidence TEXT NOT NULL DEFAULT '',
                  game_name TEXT NOT NULL DEFAULT '',
                  view_count INTEGER,
                  like_count INTEGER,
                  comment_count INTEGER,
                  scheduled_start TEXT,
                  actual_start TEXT,
                  actual_end TEXT,
                  live_state TEXT NOT NULL DEFAULT 'video',
                  current_concurrent INTEGER,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS videos_live_idx ON videos(live_state, scheduled_start);
                CREATE INDEX IF NOT EXISTS videos_channel_idx ON videos(channel_id, published_at DESC);

                CREATE TABLE IF NOT EXISTS video_classification_overrides (
                  video_id TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
                  content_topics TEXT NOT NULL,
                  game_name TEXT NOT NULL DEFAULT '',
                  note TEXT NOT NULL DEFAULT '',
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS concurrency_samples (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                  captured_at TEXT NOT NULL,
                  concurrent_viewers INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS concurrency_lookup_idx
                  ON concurrency_samples(video_id, captured_at);

                CREATE TABLE IF NOT EXISTS video_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                  captured_at TEXT NOT NULL,
                  view_count INTEGER,
                  like_count INTEGER,
                  comment_count INTEGER
                );
                CREATE INDEX IF NOT EXISTS video_snapshots_lookup_idx
                  ON video_snapshots(video_id, captured_at);

                CREATE TABLE IF NOT EXISTS quota_usage (
                  quota_day TEXT NOT NULL,
                  bucket TEXT NOT NULL,
                  units INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(quota_day, bucket)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_import_batches (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  filename TEXT NOT NULL,
                  file_hash TEXT NOT NULL,
                  imported_at TEXT NOT NULL,
                  report_count INTEGER NOT NULL DEFAULT 1,
                  row_count INTEGER NOT NULL DEFAULT 0,
                  inserted_count INTEGER NOT NULL DEFAULT 0,
                  duplicate_count INTEGER NOT NULL DEFAULT 0,
                  conflict_count INTEGER NOT NULL DEFAULT 0,
                  date_start TEXT,
                  date_end TEXT,
                  UNIQUE(channel_id, file_hash)
                );
                CREATE INDEX IF NOT EXISTS creator_batches_channel_idx
                  ON creator_import_batches(channel_id, imported_at DESC);

                CREATE TABLE IF NOT EXISTS creator_analytics_rows (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_id INTEGER NOT NULL REFERENCES creator_import_batches(id) ON DELETE CASCADE,
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  report_name TEXT NOT NULL,
                  event_date TEXT,
                  published_at TEXT,
                  duration_seconds REAL,
                  video_id TEXT,
                  video_title TEXT,
                  row_kind TEXT NOT NULL DEFAULT 'detail',
                  views REAL,
                  engaged_views REAL,
                  watch_time_hours REAL,
                  average_view_duration_seconds REAL,
                  average_percentage_viewed REAL,
                  impressions REAL,
                  impressions_ctr REAL,
                  subscribers_net REAL,
                  subscribers_gained REAL,
                  subscribers_lost REAL,
                  likes REAL,
                  comments REAL,
                  shares REAL,
                  unique_viewers REAL,
                  returning_viewers REAL,
                  estimated_revenue REAL,
                  dimensions_json TEXT NOT NULL DEFAULT '{}',
                  natural_key TEXT NOT NULL,
                  row_hash TEXT NOT NULL,
                  conflict_status INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  UNIQUE(channel_id, row_hash)
                );
                CREATE INDEX IF NOT EXISTS creator_rows_channel_idx
                  ON creator_analytics_rows(channel_id, event_date DESC, id DESC);
                CREATE INDEX IF NOT EXISTS creator_rows_key_idx
                  ON creator_analytics_rows(channel_id, natural_key, id DESC);

                CREATE TABLE IF NOT EXISTS creator_manual_metrics (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  metric_date TEXT NOT NULL,
                  video_id TEXT NOT NULL DEFAULT '',
                  metric_name TEXT NOT NULL,
                  metric_value REAL NOT NULL,
                  note TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(channel_id, metric_date, video_id, metric_name)
                );
                CREATE INDEX IF NOT EXISTS creator_manual_channel_idx
                  ON creator_manual_metrics(channel_id, metric_date DESC);

                CREATE TABLE IF NOT EXISTS creator_workspace_channels (
                  channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id) ON DELETE CASCADE,
                  added_at TEXT NOT NULL,
                  display_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS creator_workspace_order_idx
                  ON creator_workspace_channels(display_order, added_at);

                CREATE TABLE IF NOT EXISTS creator_oauth_connections (
                  channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id) ON DELETE CASCADE,
                  connected_at TEXT NOT NULL,
                  last_sync_at TEXT,
                  last_data_date TEXT,
                  last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS creator_oauth_summary_metrics (
                  channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id) ON DELETE CASCADE,
                  date_start TEXT NOT NULL,
                  date_end TEXT NOT NULL,
                  metrics_json TEXT NOT NULL,
                  synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_oauth_daily_metrics (
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  event_date TEXT NOT NULL,
                  metrics_json TEXT NOT NULL,
                  synced_at TEXT NOT NULL,
                  PRIMARY KEY(channel_id,event_date)
                );
                CREATE INDEX IF NOT EXISTS creator_oauth_daily_channel_idx
                  ON creator_oauth_daily_metrics(channel_id,event_date DESC);

                CREATE TABLE IF NOT EXISTS creator_oauth_video_metrics (
                  channel_id TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                  video_id TEXT NOT NULL,
                  title TEXT,
                  thumbnail_url TEXT,
                  published_at TEXT,
                  live_at TEXT,
                  metrics_json TEXT NOT NULL,
                  synced_at TEXT NOT NULL,
                  PRIMARY KEY(channel_id,video_id)
                );
                CREATE INDEX IF NOT EXISTS creator_oauth_video_channel_idx
                  ON creator_oauth_video_metrics(channel_id,synced_at DESC);

                CREATE TABLE IF NOT EXISTS manual_refresh_queue (
                  channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id) ON DELETE CASCADE,
                  queued_at TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS manual_refresh_queue_time_idx
                  ON manual_refresh_queue(queued_at);
                """
            )
            channel_columns = {
                row["name"] for row in self.connection.execute("PRAGMA table_info(channels)").fetchall()
            }
            if "category" not in channel_columns:
                self.connection.execute(
                    "ALTER TABLE channels ADD COLUMN category TEXT NOT NULL DEFAULT '未分類'"
                )
            if "organization_name" not in channel_columns:
                self.connection.execute(
                    "ALTER TABLE channels ADD COLUMN organization_name TEXT NOT NULL DEFAULT ''"
                )
            if "manual_tags" not in channel_columns:
                self.connection.execute(
                    "ALTER TABLE channels ADD COLUMN manual_tags TEXT NOT NULL DEFAULT '[]'"
                )
            activity_columns = {
                "activity_status": "TEXT NOT NULL DEFAULT '活動中'",
                "activity_status_source": "TEXT NOT NULL DEFAULT 'automatic'",
                "activity_status_confidence": "TEXT NOT NULL DEFAULT 'normal'",
                "activity_status_reason": "TEXT NOT NULL DEFAULT ''",
                "activity_status_detected_at": "TEXT",
                "activity_status_reviewed_at": "TEXT",
                "activity_status_manual_lock": "INTEGER NOT NULL DEFAULT 0",
                "last_activity_at": "TEXT",
            }
            for column, definition in activity_columns.items():
                if column not in channel_columns:
                    self.connection.execute(f"ALTER TABLE channels ADD COLUMN {column} {definition}")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS channels_category_idx ON channels(category, subscriber_count DESC)"
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS channels_organization_idx ON channels(organization_name, subscriber_count DESC)"
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS channels_activity_idx ON channels(activity_status, subscriber_count DESC)"
            )
            creator_row_columns = {
                row["name"] for row in self.connection.execute(
                    "PRAGMA table_info(creator_analytics_rows)"
                ).fetchall()
            }
            for column, definition in {
                "published_at": "TEXT",
                "duration_seconds": "REAL",
                "row_kind": "TEXT NOT NULL DEFAULT 'detail'",
                "engaged_views": "REAL",
            }.items():
                if column not in creator_row_columns:
                    self.connection.execute(
                        f"ALTER TABLE creator_analytics_rows ADD COLUMN {column} {definition}"
                    )
            oauth_video_columns = {
                row["name"] for row in self.connection.execute(
                    "PRAGMA table_info(creator_oauth_video_metrics)"
                ).fetchall()
            }
            for column in ("title", "thumbnail_url", "published_at", "live_at"):
                if column not in oauth_video_columns:
                    self.connection.execute(
                        f"ALTER TABLE creator_oauth_video_metrics ADD COLUMN {column} TEXT"
                    )
            video_columns = {
                row["name"] for row in self.connection.execute("PRAGMA table_info(videos)").fetchall()
            }
            content_type_added = "content_type" not in video_columns
            if "tags" not in video_columns:
                self.connection.execute("ALTER TABLE videos ADD COLUMN tags TEXT")
            content_tags_added = "content_tags" not in video_columns
            if content_tags_added:
                self.connection.execute(
                    "ALTER TABLE videos ADD COLUMN content_tags TEXT NOT NULL DEFAULT '[]'"
                )
            if content_type_added:
                self.connection.execute(
                    "ALTER TABLE videos ADD COLUMN content_type TEXT NOT NULL DEFAULT '其他'"
                )
            for column, definition in {
                "classification_source": "TEXT NOT NULL DEFAULT '未辨識'",
                "classification_evidence": "TEXT NOT NULL DEFAULT ''",
                "game_name": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if column not in video_columns:
                    self.connection.execute(f"ALTER TABLE videos ADD COLUMN {column} {definition}")
            classifier_row = self.connection.execute(
                "SELECT value FROM app_settings WHERE key='content_classifier_version'"
            ).fetchone()
            try:
                classifier_version = int(json.loads(classifier_row["value"])) if classifier_row else 0
            except (TypeError, ValueError, json.JSONDecodeError):
                classifier_version = 0
            if content_type_added or content_tags_added or classifier_version < 4:
                confirmed_game_names = [
                    row["game_name"] for row in self.connection.execute(
                        "SELECT DISTINCT game_name FROM video_classification_overrides WHERE game_name<>''"
                    ).fetchall()
                ]
                existing_videos = self.connection.execute(
                    """SELECT v.video_id,v.title,v.description,v.category_id,v.tags,v.live_state,
                              o.content_topics AS override_topics,o.game_name AS override_game_name,
                              o.note AS override_note
                         FROM videos v LEFT JOIN video_classification_overrides o ON o.video_id=v.video_id"""
                ).fetchall()
                classification_rows: list[tuple[str, str, str, str, str, str]] = []
                for row in existing_videos:
                    automatic = classify_content_details(
                        row["title"], row["description"] or "", parse_json_list(row["tags"]),
                        row["category_id"], row["live_state"], confirmed_game_names
                    )
                    if row["override_topics"]:
                        topics = [
                            label for label in parse_json_list(row["override_topics"])
                            if label in CONTENT_TOPIC_ORDER and label != "其他"
                        ] or ["其他"]
                        override_evidence = row["override_note"] or f"使用者已確認：{' + '.join(topics)}"
                        if row["override_game_name"]:
                            override_evidence += f"；遊戲：{row['override_game_name']}"
                        details = {
                            "content_type": " + ".join(topics),
                            "labels": [*topics, *automatic["attributes"]],
                            "classification_source": "人工確認",
                            "classification_evidence": override_evidence,
                            "game_name": row["override_game_name"] or "",
                        }
                    else:
                        details = automatic
                    classification_rows.append((
                        details["content_type"], json.dumps(details["labels"], ensure_ascii=False),
                        details["classification_source"], details["classification_evidence"],
                        details["game_name"], row["video_id"],
                    ))
                self.connection.executemany(
                    """UPDATE videos SET content_type=?,content_tags=?,classification_source=?,
                              classification_evidence=?,game_name=? WHERE video_id=?""",
                    classification_rows,
                )
                now = utc_now()
                self.connection.execute(
                    """INSERT INTO app_settings(key,value,updated_at) VALUES ('content_classifier_version','4',?)
                       ON CONFLICT(key) DO UPDATE SET value='4',updated_at=excluded.updated_at""",
                    (now,),
                )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS videos_content_idx ON videos(content_type, published_at DESC)"
            )
            self.connection.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self.lock:
            cursor = self.connection.execute(sql, params)
            self.connection.commit()
            return cursor

    def executemany(self, sql: str, params: Iterable[tuple[Any, ...]]) -> None:
        with self.lock:
            self.connection.executemany(sql, params)
            self.connection.commit()

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with self.lock:
            row = self.connection.execute(sql, params).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def export_public_monitoring(
        self,
        *,
        include_blacklist: bool = False,
        include_source_evidence: bool = False,
    ) -> tuple[str, bytes, dict[str, Any]]:
        with self.lock:
            return export_public_package(
                self.connection,
                include_blacklist=include_blacklist,
                include_source_evidence=include_source_evidence,
            )

    def preview_public_monitoring(self, package_bytes: bytes) -> dict[str, Any]:
        return preview_public_package(package_bytes)

    def import_public_monitoring(self, package_bytes: bytes, mode: str) -> dict[str, Any]:
        with self.lock:
            return import_public_package(self.connection, package_bytes, mode)

    def get_setting(self, key: str, default: Any) -> Any:
        raw = self.scalar("SELECT value FROM app_settings WHERE key=?", (key,))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return default

    def set_settings(self, values: dict[str, Any]) -> None:
        now = utc_now()
        rows = [(key, json.dumps(value, ensure_ascii=False), now) for key, value in values.items()]
        with self.lock:
            self.connection.executemany(
                """INSERT INTO app_settings(key,value,updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                rows,
            )
            self.connection.commit()

    def create_discovery_batch(self, terms: list[str], pages_per_term: int) -> int:
        cursor = self.execute(
            """INSERT INTO discovery_batches
               (started_at,status,discovery_terms_json,pages_per_term)
               VALUES (?,'running',?,?)""",
            (utc_now(), json.dumps(terms, ensure_ascii=False), pages_per_term),
        )
        return int(cursor.lastrowid)

    def update_discovery_batch(self, batch_id: int, values: dict[str, Any]) -> None:
        allowed = {
            "completed_at", "status", "candidate_count", "examined_count",
            "eligible_count", "new_count", "refreshed_count", "below_threshold_count",
            "review_count", "excluded_count", "rejected_count", "error",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        assignments = ",".join(f"{key}=?" for key in updates)
        self.execute(
            f"UPDATE discovery_batches SET {assignments} WHERE id=?",
            (*updates.values(), batch_id),
        )

    def upsert_discovery_candidate(self, candidate: dict[str, Any]) -> None:
        now = str(candidate.get("discovered_at") or utc_now())
        self.execute(
            """INSERT INTO discovery_candidates (
                 batch_id,channel_id,title,handle,description,keywords,country,thumbnail_url,
                 subscriber_count,view_count,video_count,hidden_subscriber_count,
                 uploads_playlist_id,search_terms_json,match_term,match_field,match_excerpt,
                 validation_status,unlisted_reason,handling_status,handling_note,
                 discovered_at,handled_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(batch_id,channel_id) DO UPDATE SET
                 title=excluded.title,handle=excluded.handle,description=excluded.description,
                 keywords=excluded.keywords,country=excluded.country,
                 thumbnail_url=excluded.thumbnail_url,subscriber_count=excluded.subscriber_count,
                 view_count=excluded.view_count,video_count=excluded.video_count,
                 hidden_subscriber_count=excluded.hidden_subscriber_count,
                 uploads_playlist_id=excluded.uploads_playlist_id,
                 search_terms_json=excluded.search_terms_json,
                 match_term=excluded.match_term,match_field=excluded.match_field,
                 match_excerpt=excluded.match_excerpt,
                 validation_status=excluded.validation_status,
                 unlisted_reason=excluded.unlisted_reason,
                 handling_status=CASE
                   WHEN discovery_candidates.handling_status IN ('manual_approved','manual_excluded')
                   THEN discovery_candidates.handling_status
                   ELSE excluded.handling_status
                 END,
                 handling_note=CASE
                   WHEN discovery_candidates.handling_status IN ('manual_approved','manual_excluded')
                   THEN discovery_candidates.handling_note
                   ELSE excluded.handling_note
                 END,
                 handled_at=CASE
                   WHEN discovery_candidates.handling_status IN ('manual_approved','manual_excluded')
                   THEN discovery_candidates.handled_at
                   ELSE excluded.handled_at
                 END""",
            (
                int(candidate["batch_id"]),
                str(candidate["channel_id"]),
                str(candidate.get("title") or candidate["channel_id"])[:300],
                candidate.get("handle"),
                str(candidate.get("description") or "")[:5000],
                str(candidate.get("keywords") or "")[:3000],
                candidate.get("country"),
                candidate.get("thumbnail_url"),
                candidate.get("subscriber_count"),
                candidate.get("view_count"),
                candidate.get("video_count"),
                1 if candidate.get("hidden_subscriber_count") else 0,
                candidate.get("uploads_playlist_id"),
                json.dumps(candidate.get("search_terms") or [], ensure_ascii=False),
                candidate.get("match_term"),
                candidate.get("match_field"),
                candidate.get("match_excerpt"),
                str(candidate.get("validation_status") or "pending"),
                str(candidate.get("unlisted_reason") or ""),
                str(candidate.get("handling_status") or "pending"),
                str(candidate.get("handling_note") or ""),
                now,
                candidate.get("handled_at"),
            ),
        )

    def discovery_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        rows = self.rows(
            "SELECT * FROM discovery_candidates WHERE id=?", (candidate_id,)
        )
        return rows[0] if rows else None

    def update_candidate_handling(
        self,
        candidate_id: int,
        status: str,
        note: str,
    ) -> None:
        self.execute(
            """UPDATE discovery_candidates
               SET handling_status=?,handling_note=?,handled_at=?
               WHERE id=?""",
            (status, note[:500], utc_now(), candidate_id),
        )

    def add_workspace_channel(self, channel_id: str) -> None:
        next_order = int(self.scalar(
            "SELECT COALESCE(MAX(display_order),-1)+1 FROM creator_workspace_channels"
        ) or 0)
        self.execute(
            """INSERT INTO creator_workspace_channels(channel_id,added_at,display_order)
               VALUES (?,?,?) ON CONFLICT(channel_id) DO NOTHING""",
            (channel_id, utc_now(), next_order),
        )

    def remove_workspace_channel(self, channel_id: str) -> bool:
        cursor = self.execute(
            "DELETE FROM creator_workspace_channels WHERE channel_id=?", (channel_id,)
        )
        return cursor.rowcount > 0

    def workspace_channel_ids(self) -> list[str]:
        return [row["channel_id"] for row in self.rows(
            """SELECT channel_id FROM creator_workspace_channels
               ORDER BY display_order,added_at,channel_id"""
        )]

    def enqueue_manual_refresh(self, channel_id: str) -> int:
        self.execute(
            """INSERT INTO manual_refresh_queue(channel_id,queued_at,attempts,last_error)
               VALUES (?,?,0,'') ON CONFLICT(channel_id) DO UPDATE SET queued_at=excluded.queued_at""",
            (channel_id, utc_now()),
        )
        return int(self.scalar("SELECT COUNT(*) FROM manual_refresh_queue") or 0)

    def manual_refresh_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.rows(
            """SELECT channel_id,queued_at,attempts,last_error FROM manual_refresh_queue
               ORDER BY queued_at,channel_id LIMIT ?""",
            (limit,),
        )

    def complete_manual_refresh(self, channel_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(channel_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.execute(
            f"DELETE FROM manual_refresh_queue WHERE channel_id IN ({placeholders})", tuple(ids)
        )

    def fail_manual_refresh(self, channel_ids: Iterable[str], error: str) -> None:
        ids = list(dict.fromkeys(channel_ids))
        if not ids:
            return
        self.executemany(
            """UPDATE manual_refresh_queue SET attempts=attempts+1,last_error=?
               WHERE channel_id=?""",
            [(error[:300], channel_id) for channel_id in ids],
        )

    def update_channel_metadata(
        self,
        channel_id: str,
        category: str,
        organization_name: str = "",
        manual_tags: Iterable[str] = (),
        activity_status: str | None = None,
    ) -> dict[str, Any] | None:
        category = category.strip() or "未分類"
        organization_name = organization_name.strip()
        tags = parse_json_list(list(manual_tags))
        now = utc_now()
        with self.lock:
            if activity_status is None:
                cursor = self.connection.execute(
                    """UPDATE channels SET category=?, organization_name=?, manual_tags=?, updated_at=?
                       WHERE channel_id=?""",
                    (category, organization_name, json.dumps(tags, ensure_ascii=False), now, channel_id),
                )
            else:
                cursor = self.connection.execute(
                    """UPDATE channels SET category=?, organization_name=?, manual_tags=?,
                              activity_status=?, activity_status_source='manual',
                              activity_status_confidence='confirmed', activity_status_manual_lock=1,
                              activity_status_reviewed_at=?, updated_at=?
                       WHERE channel_id=?""",
                    (
                        category, organization_name, json.dumps(tags, ensure_ascii=False),
                        activity_status, now, now, channel_id,
                    ),
                )
            self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return {
            "channel_id": channel_id,
            "category": category,
            "organization_name": organization_name,
            "manual_tags": tags,
            "activity_status": activity_status,
        }

    def update_automatic_activity(
        self,
        channel_id: str,
        status: str,
        confidence: str,
        reason: str,
        last_activity_at: str | None,
    ) -> None:
        now = utc_now()
        with self.lock:
            current = self.connection.execute(
                """SELECT activity_status,activity_status_manual_lock,activity_status_reviewed_at
                   FROM channels WHERE channel_id=?""",
                (channel_id,),
            ).fetchone()
            if not current:
                return
            if int(current["activity_status_manual_lock"] or 0):
                manual_reason = reason
                last_activity = parse_api_time(last_activity_at)
                reviewed_at = parse_api_time(current["activity_status_reviewed_at"])
                has_post_review_activity = bool(
                    last_activity
                    and (not reviewed_at or last_activity >= reviewed_at)
                )
                if current["activity_status"] == "已確認畢業" and has_post_review_activity:
                    manual_reason = f"確認畢業後偵測到新活動：{last_activity_at}；請檢查是否復出"
                self.connection.execute(
                    """UPDATE channels SET last_activity_at=?, activity_status_reason=?
                       WHERE channel_id=?""",
                    (last_activity_at, manual_reason, channel_id),
                )
            else:
                self.connection.execute(
                    """UPDATE channels SET activity_status=?,activity_status_source='automatic',
                              activity_status_confidence=?,activity_status_reason=?,
                              activity_status_detected_at=?,last_activity_at=?
                       WHERE channel_id=?""",
                    (status, confidence, reason, now, last_activity_at, channel_id),
                )
            self.connection.commit()

    def creator_import_batch(
        self,
        channel_id: str,
        filename: str,
        file_hash: str,
        reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = self.rows(
            """SELECT * FROM creator_import_batches
               WHERE channel_id=? AND file_hash=?""",
            (channel_id, file_hash),
        )
        if existing:
            return {**existing[0], "already_imported": True}

        parsed_rows = [row for report in reports for row in report["rows"]]
        dates = [row["event_date"] for row in parsed_rows if row["event_date"]]
        now = utc_now()
        with self.lock:
            cursor = self.connection.execute(
                """INSERT INTO creator_import_batches
                   (channel_id,filename,file_hash,imported_at,report_count,row_count,
                    inserted_count,duplicate_count,conflict_count,date_start,date_end)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    channel_id, filename, file_hash, now, len(reports), len(parsed_rows),
                    0, 0, 0, min(dates) if dates else None, max(dates) if dates else None,
                ),
            )
            batch_id = int(cursor.lastrowid)
            inserted = duplicate = conflicts = 0
            for row in parsed_rows:
                duplicate_row = self.connection.execute(
                    "SELECT id FROM creator_analytics_rows WHERE channel_id=? AND row_hash=?",
                    (channel_id, row["row_hash"]),
                ).fetchone()
                if duplicate_row:
                    duplicate += 1
                    continue
                existing_key = self.connection.execute(
                    """SELECT id FROM creator_analytics_rows
                       WHERE channel_id=? AND natural_key=? ORDER BY id DESC LIMIT 1""",
                    (channel_id, row["natural_key"]),
                ).fetchone()
                conflict = 1 if existing_key else 0
                if conflict:
                    conflicts += 1
                    self.connection.execute(
                        """UPDATE creator_analytics_rows SET conflict_status=1
                           WHERE channel_id=? AND natural_key=?""",
                        (channel_id, row["natural_key"]),
                    )
                metrics = row["metrics"]
                metric_columns = ",".join(CREATOR_METRICS)
                placeholders = ",".join(
                    "?" for _ in range(9 + len(CREATOR_METRICS) + 5)
                )
                self.connection.execute(
                    f"""INSERT INTO creator_analytics_rows (
                          batch_id,channel_id,report_name,event_date,published_at,
                          duration_seconds,row_kind,video_id,video_title,{metric_columns},
                          dimensions_json,natural_key,row_hash,conflict_status,created_at
                        ) VALUES ({placeholders})""",
                    (
                        batch_id, channel_id, row["report_name"], row["event_date"],
                        row.get("published_at"), row.get("duration_seconds"),
                        row.get("row_kind", "detail"), row["video_id"], row["video_title"],
                        *[metrics.get(metric) for metric in CREATOR_METRICS],
                        json.dumps(row["dimensions"], ensure_ascii=False), row["natural_key"],
                        row["row_hash"], conflict, now,
                    ),
                )
                inserted += 1
            self.connection.execute(
                """UPDATE creator_import_batches
                   SET inserted_count=?,duplicate_count=?,conflict_count=? WHERE id=?""",
                (inserted, duplicate, conflicts, batch_id),
            )
            self.connection.commit()
        return {
            "id": batch_id,
            "channel_id": channel_id,
            "filename": filename,
            "file_hash": file_hash,
            "imported_at": now,
            "report_count": len(reports),
            "row_count": len(parsed_rows),
            "inserted_count": inserted,
            "duplicate_count": duplicate,
            "conflict_count": conflicts,
            "date_start": min(dates) if dates else None,
            "date_end": max(dates) if dates else None,
            "already_imported": False,
        }

    def delete_creator_import(self, channel_id: str, batch_id: int) -> dict[str, Any] | None:
        rows = self.rows(
            "SELECT id,filename FROM creator_import_batches WHERE id=? AND channel_id=?",
            (batch_id, channel_id),
        )
        if not rows:
            return None
        natural_keys = [row["natural_key"] for row in self.rows(
            "SELECT DISTINCT natural_key FROM creator_analytics_rows WHERE batch_id=?",
            (batch_id,),
        )]
        self.execute("DELETE FROM creator_import_batches WHERE id=? AND channel_id=?", (batch_id, channel_id))
        for natural_key in natural_keys:
            remaining = int(self.scalar(
                "SELECT COUNT(*) FROM creator_analytics_rows WHERE channel_id=? AND natural_key=?",
                (channel_id, natural_key),
            ) or 0)
            if remaining <= 1:
                self.execute(
                    """UPDATE creator_analytics_rows SET conflict_status=0
                       WHERE channel_id=? AND natural_key=?""",
                    (channel_id, natural_key),
                )
        return rows[0]

    def upsert_creator_manual_metric(
        self,
        channel_id: str,
        metric_date: str,
        video_id: str,
        metric_name: str,
        metric_value: float,
        note: str,
    ) -> dict[str, Any]:
        now = utc_now()
        self.execute(
            """INSERT INTO creator_manual_metrics
               (channel_id,metric_date,video_id,metric_name,metric_value,note,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(channel_id,metric_date,video_id,metric_name) DO UPDATE SET
                 metric_value=excluded.metric_value,note=excluded.note,updated_at=excluded.updated_at""",
            (channel_id, metric_date, video_id, metric_name, metric_value, note, now, now),
        )
        return self.rows(
            """SELECT * FROM creator_manual_metrics
               WHERE channel_id=? AND metric_date=? AND video_id=? AND metric_name=?""",
            (channel_id, metric_date, video_id, metric_name),
        )[0]

    def delete_creator_manual_metric(self, channel_id: str, metric_id: int) -> bool:
        cursor = self.execute(
            "DELETE FROM creator_manual_metrics WHERE id=? AND channel_id=?",
            (metric_id, channel_id),
        )
        return cursor.rowcount > 0

    def upsert_channel(
        self,
        item: dict[str, Any],
        status: str | None = None,
        evidence: tuple[str, str, str] | None = None,
    ) -> None:
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        branding = item.get("brandingSettings", {}).get("channel", {})
        uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
        match_term, match_field, match_excerpt = evidence or (None, None, None)
        now = utc_now()
        values = (
            item["id"], snippet.get("title", item["id"]), snippet.get("customUrl"),
            snippet.get("description", ""), branding.get("keywords", ""), snippet.get("country"),
            thumbnail, int(statistics["subscriberCount"]) if statistics.get("subscriberCount") else None,
            int(statistics["viewCount"]) if statistics.get("viewCount") else None,
            int(statistics["videoCount"]) if statistics.get("videoCount") else None,
            1 if statistics.get("hiddenSubscriberCount") else 0, uploads, status or "review",
            match_term, match_field, match_excerpt, now, now, now,
        )
        self.execute(
            """
            INSERT INTO channels (
              channel_id,title,handle,description,keywords,country,thumbnail_url,
              subscriber_count,view_count,video_count,hidden_subscriber_count,uploads_playlist_id,
              discovery_status,match_term,match_field,match_excerpt,created_at,updated_at,last_stats_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(channel_id) DO UPDATE SET
              title=excluded.title, handle=excluded.handle, description=excluded.description,
              keywords=excluded.keywords, country=excluded.country, thumbnail_url=excluded.thumbnail_url,
              subscriber_count=excluded.subscriber_count, view_count=excluded.view_count,
              video_count=excluded.video_count, hidden_subscriber_count=excluded.hidden_subscriber_count,
              uploads_playlist_id=excluded.uploads_playlist_id,
              discovery_status=CASE WHEN excluded.match_term IS NULL
                THEN channels.discovery_status ELSE excluded.discovery_status END,
              match_term=COALESCE(excluded.match_term, channels.match_term),
              match_field=COALESCE(excluded.match_field, channels.match_field),
              match_excerpt=COALESCE(excluded.match_excerpt, channels.match_excerpt),
              updated_at=excluded.updated_at, last_stats_at=excluded.last_stats_at
            """,
            values,
        )
        if status in {"eligible", "owned"} or (status is None and self.scalar(
            "SELECT discovery_status FROM channels WHERE channel_id=?", (item["id"],)
        ) in {"eligible", "owned"}):
            self.execute(
                """INSERT INTO channel_snapshots
                   (channel_id,captured_at,subscriber_count,view_count,video_count)
                   VALUES (?,?,?,?,?)""",
                (item["id"], now, values[7], values[8], values[9]),
            )

    def is_excluded(self, channel_id: str) -> bool:
        return bool(self.scalar(
            "SELECT 1 FROM excluded_channels WHERE channel_id=?", (channel_id,)
        ))

    def exclude_channel(self, channel_id: str) -> dict[str, Any] | None:
        rows = self.rows(
            "SELECT channel_id,title FROM channels WHERE channel_id=?", (channel_id,)
        )
        if not rows:
            return None
        channel = rows[0]
        self.execute(
            """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
               VALUES (?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 title=excluded.title, reason=excluded.reason, excluded_at=excluded.excluded_at""",
            (channel_id, channel["title"], "manual", utc_now()),
        )
        self.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        return channel

    def exclude_candidate_channel(self, channel_id: str, title: str) -> dict[str, Any]:
        now = utc_now()
        self.execute(
            """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
               VALUES (?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 title=excluded.title,reason=excluded.reason,excluded_at=excluded.excluded_at""",
            (channel_id, title, "candidate-review", now),
        )
        self.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        return {"channel_id": channel_id, "title": title}

    def restore_excluded(self, channel_id: str) -> None:
        self.execute("DELETE FROM excluded_channels WHERE channel_id=?", (channel_id,))

    def upsert_video(self, item: dict[str, Any]) -> None:
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        details = item.get("contentDetails", {})
        live = item.get("liveStreamingDetails", {})
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("medium") or thumbnails.get("high") or thumbnails.get("default") or {}).get("url")
        if live.get("actualEndTime"):
            live_state = "completed"
        elif live.get("actualStartTime"):
            live_state = "live"
        elif live.get("scheduledStartTime"):
            live_state = "upcoming"
        else:
            live_state = "video"
        concurrent = int(live["concurrentViewers"]) if live.get("concurrentViewers") else None
        tags = [str(tag) for tag in snippet.get("tags", []) if str(tag).strip()]
        confirmed_game_names = [
            row["game_name"] for row in self.rows(
                "SELECT DISTINCT game_name FROM video_classification_overrides WHERE game_name<>''"
            )
        ]
        automatic = classify_content_details(
            snippet.get("title", item["id"]),
            snippet.get("description", ""),
            tags,
            snippet.get("categoryId"),
            live_state,
            confirmed_game_names,
        )
        override_rows = self.rows(
            "SELECT content_topics,game_name,note FROM video_classification_overrides WHERE video_id=?",
            (item["id"],),
        )
        if override_rows:
            override = override_rows[0]
            topics = [
                label for label in parse_json_list(override["content_topics"])
                if label in CONTENT_TOPIC_ORDER and label != "其他"
            ] or ["其他"]
            override_evidence = override["note"] or f"使用者已確認：{' + '.join(topics)}"
            if override["game_name"]:
                override_evidence += f"；遊戲：{override['game_name']}"
            classification = {
                "content_type": " + ".join(topics),
                "labels": [*topics, *automatic["attributes"]],
                "classification_source": "人工確認",
                "classification_evidence": override_evidence,
                "game_name": override["game_name"] or "",
            }
        else:
            classification = automatic
        now = utc_now()
        self.execute(
            """
            INSERT INTO videos (
              video_id,channel_id,title,description,thumbnail_url,published_at,duration_seconds,
              category_id,tags,content_type,content_tags,classification_source,classification_evidence,
              game_name,view_count,like_count,comment_count,scheduled_start,actual_start,actual_end,
              live_state,current_concurrent,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
              title=excluded.title, description=excluded.description, thumbnail_url=excluded.thumbnail_url,
              duration_seconds=excluded.duration_seconds, category_id=excluded.category_id,
              tags=excluded.tags, content_type=excluded.content_type, content_tags=excluded.content_tags,
              classification_source=excluded.classification_source,
              classification_evidence=excluded.classification_evidence, game_name=excluded.game_name,
              view_count=excluded.view_count, like_count=excluded.like_count,
              comment_count=excluded.comment_count, scheduled_start=excluded.scheduled_start,
              actual_start=excluded.actual_start, actual_end=excluded.actual_end,
              live_state=excluded.live_state, current_concurrent=excluded.current_concurrent,
              updated_at=excluded.updated_at
            """,
            (
                item["id"], snippet.get("channelId"), snippet.get("title", item["id"]),
                snippet.get("description", ""), thumbnail, snippet.get("publishedAt"),
                parse_duration(details.get("duration")), snippet.get("categoryId"),
                json.dumps(tags, ensure_ascii=False), classification["content_type"],
                json.dumps(classification["labels"], ensure_ascii=False),
                classification["classification_source"], classification["classification_evidence"],
                classification["game_name"],
                int(statistics["viewCount"]) if statistics.get("viewCount") else None,
                int(statistics["likeCount"]) if statistics.get("likeCount") else None,
                int(statistics["commentCount"]) if statistics.get("commentCount") else None,
                live.get("scheduledStartTime"), live.get("actualStartTime"), live.get("actualEndTime"),
                live_state, concurrent, now,
            ),
        )
        self.execute(
            """INSERT INTO video_snapshots
               (video_id,captured_at,view_count,like_count,comment_count) VALUES (?,?,?,?,?)""",
            (
                item["id"], now,
                int(statistics["viewCount"]) if statistics.get("viewCount") else None,
                int(statistics["likeCount"]) if statistics.get("likeCount") else None,
                int(statistics["commentCount"]) if statistics.get("commentCount") else None,
            ),
        )
        if live_state == "live" and concurrent is not None:
            self.execute(
                "INSERT INTO concurrency_samples(video_id,captured_at,concurrent_viewers) VALUES (?,?,?)",
                (item["id"], now, concurrent),
            )


class QuotaExceeded(RuntimeError):
    pass


class YouTubeAPIError(RuntimeError):
    def __init__(self, status_code: int, reason: str, detail: str):
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        super().__init__(f"YouTube API {status_code}: {detail[:400]}")


class YouTubeClient:
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.database = database

    def quota_day(self) -> str:
        return datetime.now(PACIFIC).date().isoformat()

    def usage(self, bucket: str) -> int:
        return int(self.database.scalar(
            "SELECT units FROM quota_usage WHERE quota_day=? AND bucket=?",
            (self.quota_day(), bucket),
        ) or 0)

    def safe_limit(self, bucket: str) -> int:
        limit = self.config.quota_search_limit if bucket == "search" else self.config.quota_general_limit
        safety = max(1, int(limit * self.config.quota_safety_percent / 100))
        return max(0, limit - safety)

    def reset_at(self) -> str:
        now = datetime.now(PACIFIC)
        reset = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=PACIFIC)
        return reset.astimezone(UTC).isoformat(timespec="seconds")

    def reserve(self, bucket: str, units: int = 1) -> None:
        if self.usage(bucket) + units > self.safe_limit(bucket):
            raise QuotaExceeded(f"{bucket} 配額已達安全上限，今日暫停呼叫")
        self.database.execute(
            """INSERT INTO quota_usage(quota_day,bucket,units) VALUES (?,?,?)
               ON CONFLICT(quota_day,bucket) DO UPDATE SET units=units+excluded.units""",
            (self.quota_day(), bucket, units),
        )

    def get(self, resource: str, params: dict[str, Any], bucket: str = "general") -> dict[str, Any]:
        if not self.config.api_key:
            raise RuntimeError("尚未設定 YOUTUBE_API_KEY")
        self.reserve(bucket)
        query = urllib.parse.urlencode({**params, "key": self.config.api_key})
        request = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/{resource}?{query}",
            headers={"Accept": "application/json", "User-Agent": "TaiVPulse/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            reason = ""
            try:
                parsed = json.loads(detail)
                reasons = parsed.get("error", {}).get("errors", [])
                if reasons:
                    reason = str(reasons[0].get("reason") or "")
                if not reason:
                    reason = str(parsed.get("error", {}).get("status") or "")
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            raise YouTubeAPIError(error.code, reason, detail) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"無法連線 YouTube API：{error.reason}") from error


class TrackerService:
    def __init__(self, config: Config):
        self.config = config
        self.edition = "personal" if getattr(config, "edition", "public") == "personal" else "public"
        self.retention_choices = (
            PERSONAL_RETENTION_CHOICES if self.edition == "personal" else PUBLIC_RETENTION_CHOICES
        )
        self.setting_choices = {
            **SETTING_CHOICES,
            "retention_days": self.retention_choices,
            "creator_retention_days": CREATOR_RETENTION_CHOICES,
        }
        self.database = Database(config.database_path)
        legacy_owned = str(self.database.get_setting("owned_channel_id", "")).strip()
        if legacy_owned and self.database.scalar(
            "SELECT 1 FROM channels WHERE channel_id=?", (legacy_owned,)
        ):
            self.database.add_workspace_channel(legacy_owned)
        self.youtube = YouTubeClient(config, self.database)
        oauth_directory = config.database_path.parent / "oauth"
        self.oauth = GoogleOAuth(
            oauth_directory,
            f"http://{getattr(config, 'host', '127.0.0.1')}:{getattr(config, 'port', 8787)}/api/creator/oauth/callback",
        )
        self.channel_candidate_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.run_lock = threading.Lock()
        self.state_lock = threading.RLock()
        defaults = {
            "min_subscribers": getattr(config, "min_subscribers", 1000),
            "live_poll_seconds": getattr(config, "live_poll_seconds", 60),
            "channel_refresh_hours": getattr(config, "channel_refresh_hours", 6),
            "upload_scan_hours": getattr(config, "upload_scan_hours", 4),
            "retention_days": getattr(config, "retention_days", 30),
            "creator_retention_days": getattr(config, "creator_retention_days", 0),
            "discovery_terms": list(SEARCH_TERMS),
        }
        self.runtime_settings = {
            key: self.database.get_setting(key, value) for key, value in defaults.items()
        }
        # Normalize legacy free-form values to the fixed menus for the selected edition.
        normalized_settings: dict[str, int] = {}
        for key, choices in self.setting_choices.items():
            value = int(self.runtime_settings[key])
            if value in choices:
                continue
            fallback = int(defaults[key])
            normalized_settings[key] = fallback if fallback in choices else choices[-1]
            self.runtime_settings[key] = normalized_settings[key]
        if normalized_settings:
            self.database.set_settings(normalized_settings)
        self.current_job: str | None = None
        self.current_job_started_at: str | None = None
        self.last_job: str | None = None
        self.last_job_finished_at: str | None = None
        self.last_job_status: str | None = None
        self.last_error: str | None = None
        self.last_warning: str | None = None
        self.discovery_batch_id: int | None = None
        self.discovery_progress: dict[str, Any] = {
            "status": "idle",
            "batch_id": None,
            "started_at": None,
            "completed_at": None,
            "current_term": None,
            "term_index": 0,
            "total_terms": 0,
            "pages_per_term": getattr(self.config, "discovery_pages_per_term", 2),
            "current_page": 0,
            "candidate_count": 0,
            "examined_count": 0,
            "eligible_count": 0,
            "new_count": 0,
            "refreshed_count": 0,
            "below_threshold_count": 0,
            "review_count": 0,
            "excluded_count": 0,
            "rejected_count": 0,
            "message": None,
            "error": None,
        }
        self.running = True
        self.last_live_poll = 0.0
        self.last_upload_dispatch = 0.0
        self.last_channel_refresh = 0.0
        self.last_manual_queue_dispatch = 0.0
        self.last_cleanup = 0.0
        self.last_oauth_sync_check = 0.0
        self.last_hourly_live_scan_slot: str | None = None

    def settings_payload(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "min_subscribers": int(self.runtime_settings["min_subscribers"]),
                "live_poll_seconds": int(self.runtime_settings["live_poll_seconds"]),
                "channel_refresh_hours": int(self.runtime_settings["channel_refresh_hours"]),
                "upload_scan_hours": int(self.runtime_settings["upload_scan_hours"]),
                "retention_days": int(self.runtime_settings["retention_days"]),
                "creator_retention_days": int(self.runtime_settings["creator_retention_days"]),
                "edition": self.edition,
                "retention_options": list(self.retention_choices),
                "creator_retention_options": list(CREATOR_RETENTION_CHOICES),
                "discovery_terms": list(self.runtime_settings["discovery_terms"]),
                "enhanced_live_scan_times": list(ENHANCED_LIVE_SCAN_TIMES),
                "enhanced_live_scan_timezone": "Asia/Taipei",
                "owned_channel_live_scan_priority": True,
            }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings_payload()
        updated: dict[str, Any] = {}
        for key, (minimum, maximum) in SETTING_LIMITS.items():
            if key not in payload:
                continue
            try:
                value = int(payload[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{key} 必須是整數") from error
            if value < minimum or value > maximum:
                raise ValueError(f"{key} 必須介於 {minimum:,} 到 {maximum:,} 之間")
            updated[key] = value

        for key, choices in self.setting_choices.items():
            if key not in payload:
                continue
            try:
                value = int(payload[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{key} 必須是整數") from error
            if value not in choices:
                allowed = "、".join(f"{choice:,}" for choice in choices)
                raise ValueError(f"{key} 必須是下列其中一項：{allowed}")
            updated[key] = value

        if "discovery_terms" in payload:
            raw_terms = payload["discovery_terms"]
            if isinstance(raw_terms, str):
                raw_terms = re.split(r"[,\n]", raw_terms)
            if not isinstance(raw_terms, list):
                raise ValueError("discovery_terms 必須是字串清單")
            terms: list[str] = []
            for raw_term in raw_terms:
                term = str(raw_term).strip()
                if term and term.casefold() not in {item.casefold() for item in terms}:
                    terms.append(term)
            if not terms or len(terms) > 12 or any(len(term) > 60 for term in terms):
                raise ValueError("搜尋字樣需有 1 到 12 組，每組最多 60 個字")
            updated["discovery_terms"] = terms

        if not updated:
            raise ValueError("沒有可更新的設定")
        current.update(updated)
        self.database.set_settings(updated)
        with self.state_lock:
            self.runtime_settings = current
        return self.settings_payload()

    def start(self) -> None:
        self.evaluate_activity_statuses()
        threading.Thread(target=self._scheduler, name="tracker-scheduler", daemon=True).start()

    def _set_job(self, job: str | None) -> None:
        with self.state_lock:
            if job:
                self.current_job_started_at = utc_now()
            elif self.current_job:
                self.last_job = self.current_job
                self.last_job_finished_at = utc_now()
                self.last_job_status = "error" if self.last_error else "completed"
                self.current_job_started_at = None
            self.current_job = job

    def _begin_discovery(self) -> None:
        terms = self.settings_payload()["discovery_terms"]
        pages_per_term = getattr(self.config, "discovery_pages_per_term", 2)
        self.discovery_batch_id = self.database.create_discovery_batch(
            terms, pages_per_term
        )
        with self.state_lock:
            self.discovery_progress = {
                "status": "running",
                "batch_id": self.discovery_batch_id,
                "started_at": utc_now(),
                "completed_at": None,
                "current_term": terms[0] if terms else None,
                "term_index": 0,
                "total_terms": len(terms),
                "pages_per_term": pages_per_term,
                "current_page": 0,
                "candidate_count": 0,
                "examined_count": 0,
                "eligible_count": 0,
                "new_count": 0,
                "refreshed_count": 0,
                "below_threshold_count": 0,
                "review_count": 0,
                "excluded_count": 0,
                "rejected_count": 0,
                "message": "準備搜尋候選頻道",
                "error": None,
            }

    def _update_discovery(self, **values: Any) -> None:
        with self.state_lock:
            self.discovery_progress.update(values)
        if self.discovery_batch_id is not None:
            self.database.update_discovery_batch(self.discovery_batch_id, values)

    def _finish_discovery(self, status: str = "completed", error: str | None = None) -> None:
        completed_at = utc_now()
        with self.state_lock:
            self.discovery_progress.update({
                "status": status,
                "completed_at": completed_at,
                "current_term": None,
                "current_page": 0,
                "message": "探索完成" if status == "completed" else "探索未完成",
                "error": error,
            })
        if self.discovery_batch_id is not None:
            self.database.update_discovery_batch(self.discovery_batch_id, {
                **self.discovery_progress,
                "completed_at": completed_at,
                "status": status,
                "error": error,
            })

    def _record_error(self, error: Exception) -> None:
        completed_at = utc_now()
        with self.state_lock:
            self.last_error = str(error)[:700]
            if self.current_job == "discover" or self.discovery_progress.get("status") == "running":
                self.discovery_progress.update({
                    "status": "error",
                    "completed_at": completed_at,
                    "message": "探索未完成",
                    "error": self.last_error,
                })
                if self.discovery_batch_id is not None:
                    self.database.update_discovery_batch(self.discovery_batch_id, {
                        **self.discovery_progress,
                        "completed_at": completed_at,
                        "status": "error",
                        "error": self.last_error,
                    })

    @staticmethod
    def _is_missing_upload_playlist(error: Exception) -> bool:
        return (
            isinstance(error, YouTubeAPIError)
            and error.status_code == 404
            and error.reason == "playlistNotFound"
        )

    def _record_upload_playlist_warning(self, channel: dict[str, Any], error: Exception) -> None:
        title = str(channel.get("title") or channel["channel_id"])
        warning = (
            f"已略過「{title}」的影片更新：YouTube 找不到或無法存取這個頻道的上傳播放清單；"
            "既有資料已保留，更新頻道資料後會再重試。"
        )
        with self.state_lock:
            self.last_warning = warning
        print(
            "[uploads-playlist-skip] "
            f"channel_id={channel['channel_id']} "
            f"playlist_id={channel['uploads_playlist_id']} "
            f"error={error}",
            file=sys.stderr,
            flush=True,
        )

    def launch_job(self, name: str, callback: Callable[[], None]) -> tuple[bool, str]:
        if not self.config.api_key and name != "oauth-analytics-sync":
            return False, "尚未設定 YOUTUBE_API_KEY"
        with self.state_lock:
            if self.current_job:
                return False, f"目前正在執行：{self.current_job}"
            self.current_job = name
            self.current_job_started_at = utc_now()
            self.last_error = None
        if name == "discover":
            self._begin_discovery()

        def runner() -> None:
            try:
                with self.run_lock:
                    callback()
            except Exception as error:  # background task must remain alive
                self._record_error(error)
            finally:
                if name == "discover" and self.discovery_progress.get("status") == "running":
                    self._finish_discovery()
                self._set_job(None)

        threading.Thread(target=runner, name=f"tracker-{name}", daemon=True).start()
        return True, "已開始搜尋候選頻道" if name == "discover" else "工作已開始"

    def _save_discovery_candidate(
        self,
        channel_id: str,
        search_terms: list[str],
        item: dict[str, Any],
        validation_status: str = "pending",
        unlisted_reason: str = "等待規則驗證",
        handling_status: str = "pending",
        evidence: tuple[str, str, str] | None = None,
    ) -> None:
        if self.discovery_batch_id is None:
            return
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        branding = item.get("brandingSettings", {}).get("channel", {})
        uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("medium") or thumbnails.get("high") or thumbnails.get("default") or {}
        ).get("url")
        match_term, match_field, match_excerpt = evidence or (None, None, None)
        self.database.upsert_discovery_candidate({
            "batch_id": self.discovery_batch_id,
            "channel_id": channel_id,
            "title": snippet.get("title") or snippet.get("channelTitle") or channel_id,
            "handle": snippet.get("customUrl"),
            "description": snippet.get("description", ""),
            "keywords": branding.get("keywords", ""),
            "country": snippet.get("country"),
            "thumbnail_url": thumbnail,
            "subscriber_count": (
                int(statistics["subscriberCount"])
                if statistics.get("subscriberCount") else None
            ),
            "view_count": (
                int(statistics["viewCount"]) if statistics.get("viewCount") else None
            ),
            "video_count": (
                int(statistics["videoCount"]) if statistics.get("videoCount") else None
            ),
            "hidden_subscriber_count": bool(statistics.get("hiddenSubscriberCount")),
            "uploads_playlist_id": uploads,
            "search_terms": search_terms,
            "match_term": match_term,
            "match_field": match_field,
            "match_excerpt": match_excerpt,
            "validation_status": validation_status,
            "unlisted_reason": unlisted_reason,
            "handling_status": handling_status,
        })

    def discover_channels(self) -> None:
        settings = self.settings_payload()
        if self.discovery_progress.get("status") != "running":
            self._begin_discovery()
        search_hits: dict[str, dict[str, Any]] = {}
        for term_index, term in enumerate(settings["discovery_terms"], start=1):
            page_token: str | None = None
            for page_index in range(1, getattr(self.config, "discovery_pages_per_term", 2) + 1):
                self._update_discovery(
                    current_term=term,
                    term_index=term_index,
                    current_page=page_index,
                    message=f"正在搜尋「{term}」第 {page_index} 頁",
                )
                params: dict[str, Any] = {
                    "part": "snippet", "type": "channel", "q": term, "maxResults": 50,
                }
                if page_token:
                    params["pageToken"] = page_token
                payload = self.youtube.get("search", params, bucket="search")
                for result in payload.get("items", []):
                    channel_id = result.get("id", {}).get("channelId")
                    if not channel_id:
                        continue
                    hit = search_hits.setdefault(channel_id, {
                        "terms": [],
                        "search_result": result,
                    })
                    if term not in hit["terms"]:
                        hit["terms"].append(term)
                    self._save_discovery_candidate(
                        channel_id,
                        hit["terms"],
                        hit["search_result"],
                    )
                self._update_discovery(candidate_count=len(search_hits))
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        self._update_discovery(
            message=f"找到 {len(search_hits)} 個候選，正在核對頻道資料"
        )
        validated_ids: set[str] = set()
        for group in chunks(sorted(search_hits)):
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                channel_id = item["id"]
                validated_ids.add(channel_id)
                with self.state_lock:
                    examined = int(self.discovery_progress["examined_count"]) + 1
                self._update_discovery(examined_count=examined)
                search_terms = search_hits[channel_id]["terms"]
                if self.database.is_excluded(channel_id):
                    with self.state_lock:
                        count = int(self.discovery_progress["excluded_count"]) + 1
                    self._update_discovery(excluded_count=count)
                    self._save_discovery_candidate(
                        channel_id,
                        search_terms,
                        item,
                        validation_status="excluded",
                        unlisted_reason="此頻道已在黑名單，不會由探索自動收錄",
                        handling_status="manual_excluded",
                    )
                    continue
                evidence = evidence_for_terms(item, settings["discovery_terms"])
                if not evidence:
                    with self.state_lock:
                        count = int(self.discovery_progress["rejected_count"]) + 1
                    self._update_discovery(rejected_count=count)
                    self._save_discovery_candidate(
                        channel_id,
                        search_terms,
                        item,
                        validation_status="rejected",
                        unlisted_reason="名稱、說明與頻道關鍵字未找到符合規則的台 V 自述",
                    )
                    continue
                statistics = item.get("statistics", {})
                hidden = bool(statistics.get("hiddenSubscriberCount"))
                subscribers = (
                    int(statistics["subscriberCount"])
                    if statistics.get("subscriberCount") else None
                )
                if hidden or subscribers is None:
                    status = "review"
                    reason = "公開訂閱數未顯示，無法自動確認是否達到收錄門檻"
                    handling = "pending"
                elif subscribers >= settings["min_subscribers"]:
                    status = "eligible"
                    reason = ""
                    handling = "auto_included"
                else:
                    status = "below_threshold"
                    reason = (
                        f"公開訂閱數 {subscribers:,}，未達自動收錄門檻 "
                        f"{settings['min_subscribers']:,}"
                    )
                    handling = "pending"
                previous = self.database.rows(
                    "SELECT discovery_status FROM channels WHERE channel_id=?", (channel_id,)
                )
                self.database.upsert_channel(item, status=status, evidence=evidence)
                self._save_discovery_candidate(
                    channel_id,
                    search_terms,
                    item,
                    validation_status=status,
                    unlisted_reason=reason,
                    handling_status=handling,
                    evidence=evidence,
                )
                if status == "eligible":
                    with self.state_lock:
                        eligible = int(self.discovery_progress["eligible_count"]) + 1
                        new_count = int(self.discovery_progress["new_count"])
                        refreshed = int(self.discovery_progress["refreshed_count"])
                    if previous and previous[0]["discovery_status"] == "eligible":
                        refreshed += 1
                    else:
                        new_count += 1
                    self._update_discovery(
                        eligible_count=eligible, new_count=new_count, refreshed_count=refreshed
                    )
                elif status == "below_threshold":
                    with self.state_lock:
                        count = int(self.discovery_progress["below_threshold_count"]) + 1
                    self._update_discovery(below_threshold_count=count)
                else:
                    with self.state_lock:
                        count = int(self.discovery_progress["review_count"]) + 1
                    self._update_discovery(review_count=count)

        for channel_id in sorted(set(search_hits) - validated_ids):
            hit = search_hits[channel_id]
            self._save_discovery_candidate(
                channel_id,
                hit["terms"],
                hit["search_result"],
                validation_status="unavailable",
                unlisted_reason="YouTube 頻道資料回應未包含此頻道，這次無法完成規則驗證",
            )
        self.evaluate_activity_statuses()
        self._finish_discovery()

    def search_channels(self, query: str) -> list[dict[str, Any]]:
        min_subscribers = self.settings_payload()["min_subscribers"]
        mode, value = channel_query_target(query)
        if not value:
            raise ValueError("請輸入 VTuber 名稱、@handle、Channel ID 或頻道網址")
        if mode == "id":
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings", "id": value,
            })
        elif mode == "handle":
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings", "forHandle": value,
            })
        else:
            search = self.youtube.get("search", {
                "part": "snippet", "type": "channel", "q": value, "maxResults": 10,
            }, bucket="search")
            ids = [
                item.get("id", {}).get("channelId") for item in search.get("items", [])
                if item.get("id", {}).get("channelId")
            ]
            if not ids:
                return []
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(ids), "maxResults": 10,
            })

        candidates: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            self.channel_candidate_cache[item["id"]] = (time.monotonic(), item)
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
            hidden = bool(statistics.get("hiddenSubscriberCount"))
            subscribers = int(statistics["subscriberCount"]) if statistics.get("subscriberCount") else None
            candidates.append({
                "channel_id": item["id"],
                "title": snippet.get("title", item["id"]),
                "handle": snippet.get("customUrl"),
                "thumbnail_url": thumbnail,
                "subscriber_count": subscribers,
                "hidden_subscriber_count": hidden,
                "description": snippet.get("description", "")[:240],
                "meets_threshold": not hidden and subscribers is not None and subscribers >= min_subscribers,
                "excluded": self.database.is_excluded(item["id"]),
                "already_added": bool(self.database.scalar(
                    "SELECT 1 FROM channels WHERE channel_id=? AND discovery_status IN ('eligible','owned')", (item["id"],)
                )),
            })
        return candidates

    def add_manual_channel(self, channel_id: str, allow_owned_exception: bool = False) -> dict[str, Any]:
        min_subscribers = self.settings_payload()["min_subscribers"]
        cached = self.channel_candidate_cache.get(channel_id)
        if cached and time.monotonic() - cached[0] <= 900:
            items = [cached[1]]
        else:
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings", "id": channel_id,
            })
            items = payload.get("items", [])
        if not items:
            raise ValueError("找不到指定的 YouTube 頻道")
        item = items[0]
        statistics = item.get("statistics", {})
        if statistics.get("hiddenSubscriberCount") and not allow_owned_exception:
            raise ValueError(f"此頻道隱藏訂閱數，無法確認是否達到 {min_subscribers:,} 訂閱")
        subscribers = int(statistics["subscriberCount"]) if statistics.get("subscriberCount") else 0
        if subscribers < min_subscribers and not allow_owned_exception:
            raise ValueError(f"此頻道目前只有 {subscribers:,} 訂閱，未達收錄門檻")
        self.database.restore_excluded(channel_id)
        snippet = item.get("snippet", {})
        status = "eligible" if subscribers >= min_subscribers and not statistics.get("hiddenSubscriberCount") else "owned"
        evidence = (
            "我的頻道" if allow_owned_exception else "手動指定",
            "指定搜尋",
            snippet.get("title", channel_id),
        )
        self.database.upsert_channel(item, status=status, evidence=evidence)
        self.evaluate_activity_status(channel_id)
        queue_count = self.database.enqueue_manual_refresh(channel_id)
        if queue_count >= 50:
            self.launch_job("manual-channel-refresh", self.refresh_manual_queue)
        return {
            "channel_id": channel_id,
            "title": snippet.get("title", channel_id),
            "discovery_status": status,
            "manual_refresh_queue_count": queue_count,
        }

    def add_owned_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.add_manual_channel(channel_id, allow_owned_exception=True)
        self.database.add_workspace_channel(channel_id)
        self.database.set_settings({"owned_channel_id": channel_id})
        return channel

    def exclude_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.database.exclude_channel(channel_id)
        if not channel:
            raise ValueError("找不到要排除的頻道")
        return channel

    def candidate_review(
        self,
        query: str = "",
        batch_id: int | None = None,
        validation_status: str = "all",
        handling_status: str = "all",
        sort: str = "discovered_at",
        direction: str = "desc",
    ) -> dict[str, Any]:
        conditions: list[str] = []
        params: list[Any] = []
        if batch_id is not None:
            conditions.append("dc.batch_id=?")
            params.append(batch_id)
        if validation_status != "all":
            conditions.append("dc.validation_status=?")
            params.append(validation_status)
        if handling_status == "included":
            conditions.append("c.discovery_status IN ('eligible','owned')")
        elif handling_status == "excluded":
            conditions.append("ex.channel_id IS NOT NULL")
        elif handling_status in {"pending", "deferred"}:
            conditions.append(
                """ex.channel_id IS NULL
                   AND (c.channel_id IS NULL OR c.discovery_status NOT IN ('eligible','owned'))"""
            )
            conditions.append("dc.handling_status=?")
            params.append(handling_status)
        if query.strip():
            pattern = f"%{query.strip()[:120]}%"
            conditions.append(
                """(dc.title LIKE ? OR COALESCE(dc.handle,'') LIKE ?
                    OR dc.channel_id LIKE ? OR dc.search_terms_json LIKE ?
                    OR COALESCE(dc.match_excerpt,'') LIKE ?
                    OR dc.unlisted_reason LIKE ?)"""
            )
            params.extend([pattern] * 6)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_columns = {
            "discovered_at": "dc.discovered_at",
            "subscriber_count": "dc.subscriber_count",
            "title": "dc.title COLLATE NOCASE",
            "validation_status": "dc.validation_status",
            "batch": "dc.batch_id",
        }
        order_column = sort_columns.get(sort, sort_columns["discovered_at"])
        order_direction = "ASC" if direction.casefold() == "asc" else "DESC"
        candidates = self.database.rows(
            f"""SELECT dc.*,db.started_at AS batch_started_at,
                       db.completed_at AS batch_completed_at,db.status AS batch_status,
                       CASE
                         WHEN ex.channel_id IS NOT NULL THEN 'excluded'
                         WHEN c.discovery_status IN ('eligible','owned') THEN 'included'
                         ELSE dc.handling_status
                       END AS current_status
                  FROM discovery_candidates dc
                  JOIN discovery_batches db ON db.id=dc.batch_id
                  LEFT JOIN channels c ON c.channel_id=dc.channel_id
                  LEFT JOIN excluded_channels ex ON ex.channel_id=dc.channel_id
                  {where}
                  ORDER BY {order_column} {order_direction},dc.id DESC
                  LIMIT 2000""",
            tuple(params),
        )
        for candidate in candidates:
            try:
                candidate["search_terms"] = json.loads(
                    candidate.pop("search_terms_json") or "[]"
                )
            except json.JSONDecodeError:
                candidate["search_terms"] = []
        batches = self.database.rows(
            """SELECT id,started_at,completed_at,status,discovery_terms_json,
                      pages_per_term,candidate_count,examined_count,eligible_count,
                      new_count,refreshed_count,below_threshold_count,review_count,
                      excluded_count,rejected_count,error
                 FROM discovery_batches ORDER BY id DESC LIMIT 100"""
        )
        for batch in batches:
            try:
                batch["discovery_terms"] = json.loads(
                    batch.pop("discovery_terms_json") or "[]"
                )
            except json.JSONDecodeError:
                batch["discovery_terms"] = []
        validation_counts = {
            row["validation_status"]: int(row["count"])
            for row in self.database.rows(
                """SELECT validation_status,COUNT(*) AS count
                     FROM discovery_candidates GROUP BY validation_status"""
            )
        }
        return {
            "candidates": candidates,
            "batches": batches,
            "validation_counts": validation_counts,
            "result_count": len(candidates),
            "quota_note": "此頁只讀取本機 SQLite；查看、搜尋、篩選與排序不會使用 YouTube Search API 配額。",
        }

    def review_candidate(self, candidate_id: int, action: str) -> dict[str, Any]:
        candidate = self.database.discovery_candidate(candidate_id)
        if not candidate:
            raise ValueError("找不到這筆候選紀錄")
        channel_id = str(candidate["channel_id"])
        title = str(candidate["title"])
        if action == "approve":
            item = {
                "id": channel_id,
                "snippet": {
                    "title": title,
                    "customUrl": candidate.get("handle"),
                    "description": candidate.get("description") or "",
                    "country": candidate.get("country"),
                    "thumbnails": {
                        "default": {"url": candidate.get("thumbnail_url")}
                    } if candidate.get("thumbnail_url") else {},
                },
                "statistics": {
                    **(
                        {"subscriberCount": str(candidate["subscriber_count"])}
                        if candidate.get("subscriber_count") is not None else {}
                    ),
                    **(
                        {"viewCount": str(candidate["view_count"])}
                        if candidate.get("view_count") is not None else {}
                    ),
                    **(
                        {"videoCount": str(candidate["video_count"])}
                        if candidate.get("video_count") is not None else {}
                    ),
                    "hiddenSubscriberCount": bool(
                        candidate.get("hidden_subscriber_count")
                    ),
                },
                "contentDetails": {
                    "relatedPlaylists": {
                        "uploads": candidate.get("uploads_playlist_id")
                    }
                },
                "brandingSettings": {
                    "channel": {"keywords": candidate.get("keywords") or ""}
                },
            }
            evidence = (
                (
                    str(candidate["match_term"]),
                    str(candidate["match_field"]),
                    str(candidate["match_excerpt"]),
                )
                if candidate.get("match_term")
                else ("人工審核", "候選審核頁", f"人工確認收錄：{title}")
            )
            self.database.restore_excluded(channel_id)
            self.database.upsert_channel(item, status="eligible", evidence=evidence)
            self.database.update_candidate_handling(
                candidate_id,
                "manual_approved",
                "已由使用者在候選審核頁確認收錄",
            )
            queue_count = self.database.enqueue_manual_refresh(channel_id)
            return {
                "message": f"已人工確認收錄 {title}",
                "channel_id": channel_id,
                "queue_count": queue_count,
            }
        if action == "exclude":
            self.database.exclude_candidate_channel(channel_id, title)
            self.database.update_candidate_handling(
                candidate_id,
                "manual_excluded",
                "已由使用者在候選審核頁排除並加入黑名單",
            )
            return {
                "message": f"已排除 {title}，後續探索不會自動加回",
                "channel_id": channel_id,
            }
        if action == "defer":
            self.database.update_candidate_handling(
                candidate_id,
                "deferred",
                "已標記為稍後處理",
            )
            return {
                "message": f"已將 {title} 標記為稍後處理",
                "channel_id": channel_id,
            }
        raise ValueError("不支援的候選處理動作")

    def update_channel_metadata(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        category = str(payload.get("category", "")).strip() or "未分類"
        organization_name = str(payload.get("organization_name", "")).strip()
        tags = parse_json_list(payload.get("manual_tags", []))
        activity_status = payload.get("activity_status")
        restore_automatic = activity_status == "自動判斷"
        if activity_status is not None:
            activity_status = str(activity_status).strip()
            if activity_status != "自動判斷" and activity_status not in ACTIVITY_STATUSES:
                raise ValueError("不支援的活動狀態")
            if restore_automatic:
                activity_status = None
        if len(category) > 40:
            raise ValueError("分類名稱最多 40 個字")
        if len(organization_name) > 80:
            raise ValueError("所屬組織最多 80 個字")
        if len(tags) > 20 or any(len(tag) > 40 for tag in tags):
            raise ValueError("標籤最多 20 個，每個最多 40 個字")
        channel = self.database.update_channel_metadata(
            channel_id, category, organization_name, tags, activity_status
        )
        if not channel:
            raise ValueError("找不到要更新的頻道")
        if restore_automatic:
            self.database.execute(
                """UPDATE channels SET activity_status_manual_lock=0,
                          activity_status_source='automatic',activity_status_reviewed_at=NULL
                   WHERE channel_id=?""",
                (channel_id,),
            )
            self.evaluate_activity_status(channel_id)
        result = self.database.rows(
            """SELECT channel_id,category,organization_name,manual_tags,activity_status,
                      activity_status_source,activity_status_confidence,activity_status_reason,
                      activity_status_detected_at,activity_status_reviewed_at,
                      activity_status_manual_lock,last_activity_at
               FROM channels WHERE channel_id=?""",
            (channel_id,),
        )[0]
        result["manual_tags"] = parse_json_list(result["manual_tags"])
        return result

    def evaluate_activity_status(self, channel_id: str) -> None:
        rows = self.database.rows(
            """SELECT channel_id,title,description,keywords FROM channels WHERE channel_id=?""",
            (channel_id,),
        )
        if not rows:
            return
        channel = rows[0]
        profile_text = " ".join([
            str(channel.get("title") or ""),
            str(channel.get("description") or ""),
            str(channel.get("keywords") or ""),
        ])
        negated_graduation = bool(re.search(r"未畢業|沒有畢業|不會畢業|尚未畢業", profile_text, re.I))
        explicit_graduation = bool(GRADUATION_PATTERN.search(profile_text)) and not negated_graduation
        explicit_hiatus = bool(HIATUS_PATTERN.search(profile_text))
        event_rows = self.database.rows(
            """SELECT COALESCE(actual_start,published_at,scheduled_start,updated_at) AS activity_at
               FROM videos WHERE channel_id=? AND COALESCE(actual_start,published_at,scheduled_start,updated_at) IS NOT NULL
               ORDER BY datetime(COALESCE(actual_start,published_at,scheduled_start,updated_at)) DESC LIMIT 30""",
            (channel_id,),
        )
        event_times = [
            parsed for row in event_rows
            if (parsed := parse_api_time(row.get("activity_at"))) is not None
        ]
        event_times.sort(reverse=True)
        last_activity = event_times[0] if event_times else None
        last_activity_at = last_activity.isoformat(timespec="seconds") if last_activity else None
        now = datetime.now(UTC)
        inactive_days = max(0, (now - last_activity).days) if last_activity else None
        intervals = [
            (event_times[index] - event_times[index + 1]).total_seconds() / 86400
            for index in range(min(len(event_times) - 1, 20))
            if event_times[index] > event_times[index + 1]
        ]
        median_interval = percentile(intervals, .5) if len(event_times) >= 5 else None
        adaptive_limit = max(180.0, float(median_interval or 0) * 6) if median_interval is not None else 365.0

        if explicit_graduation:
            status, confidence = "疑似已畢業", "high"
            match = GRADUATION_PATTERN.search(profile_text)
            reason = f"頻道名稱／簡介命中「{match.group(0) if match else '畢業或停止活動'}」；待人工確認"
        elif explicit_hiatus:
            status, confidence = "休止中", "high"
            match = HIATUS_PATTERN.search(profile_text)
            reason = f"頻道名稱／簡介命中「{match.group(0) if match else '休止'}」；待人工確認"
        elif last_activity is None:
            status, confidence = "狀態不明", "low"
            reason = "尚未累積可判斷的上片或直播紀錄"
        elif inactive_days is not None and inactive_days >= 365:
            status, confidence = "疑似已畢業", "high"
            reason = f"最後活動距今 {inactive_days} 天；已超過一年，待人工確認"
        elif inactive_days is not None and median_interval is not None and inactive_days >= adaptive_limit:
            status, confidence = "疑似已畢業", "medium"
            reason = (
                f"最後活動距今 {inactive_days} 天；平常發布中位間隔約 {median_interval:.0f} 天，"
                f"已超過判定門檻 {adaptive_limit:.0f} 天"
            )
        else:
            status, confidence = "活動中", "normal"
            cadence = f"；平常發布中位間隔約 {median_interval:.0f} 天" if median_interval is not None else ""
            reason = f"最後活動距今 {inactive_days or 0} 天{cadence}"
        self.database.update_automatic_activity(
            channel_id, status, confidence, reason, last_activity_at
        )

    def evaluate_activity_statuses(self, channel_ids: Iterable[str] | None = None) -> None:
        ids = list(channel_ids or [row["channel_id"] for row in self.database.rows(
            "SELECT channel_id FROM channels WHERE discovery_status IN ('eligible','owned')"
        )])
        for channel_id in ids:
            self.evaluate_activity_status(channel_id)

    def update_channel_category(self, channel_id: str, category: str) -> dict[str, Any]:
        value = category.strip() or "未分類"
        current = self.database.rows(
            "SELECT organization_name,manual_tags FROM channels WHERE channel_id=?", (channel_id,)
        )
        if not current:
            raise ValueError("找不到要分類的頻道")
        return self.update_channel_metadata(channel_id, {
            "category": value,
            "organization_name": current[0]["organization_name"],
            "manual_tags": parse_json_list(current[0]["manual_tags"]),
        })

    def channel_detail(self, channel_id: str) -> dict[str, Any]:
        channels = self.database.rows(
            """SELECT channel_id,title,handle,description,keywords,country,thumbnail_url,
                      subscriber_count,view_count,video_count,hidden_subscriber_count,category,
                      organization_name,manual_tags,activity_status,activity_status_source,
                      activity_status_confidence,activity_status_reason,activity_status_detected_at,
                      activity_status_reviewed_at,activity_status_manual_lock,last_activity_at,
                      discovery_status,match_term,match_field,match_excerpt,created_at,updated_at,
                      last_stats_at,last_upload_scan_at
               FROM channels WHERE channel_id=?""",
            (channel_id,),
        )
        if not channels:
            raise ValueError("找不到指定頻道")
        snapshots = self.database.rows(
            """SELECT captured_at,subscriber_count,view_count,video_count FROM (
                 SELECT captured_at,subscriber_count,view_count,video_count
                 FROM channel_snapshots WHERE channel_id=?
                 ORDER BY captured_at DESC LIMIT 500
               ) ORDER BY captured_at ASC""",
            (channel_id,),
        )
        videos = self.database.rows(
            """SELECT v.video_id,v.title,v.thumbnail_url,v.published_at,v.duration_seconds,
                      v.view_count,v.like_count,v.comment_count,v.live_state,v.current_concurrent,
                      v.scheduled_start,v.actual_start,v.actual_end,v.updated_at,
                      MAX(cs.concurrent_viewers) AS peak_concurrent,
                      COUNT(cs.id) AS concurrency_samples
               FROM videos v
               LEFT JOIN concurrency_samples cs ON cs.video_id=v.video_id
               WHERE v.channel_id=?
               GROUP BY v.video_id
               ORDER BY COALESCE(v.published_at,v.scheduled_start,v.updated_at) DESC LIMIT 150""",
            (channel_id,),
        )
        peak = self.database.scalar(
            """SELECT MAX(cs.concurrent_viewers)
               FROM concurrency_samples cs JOIN videos v ON v.video_id=cs.video_id
               WHERE v.channel_id=?""",
            (channel_id,),
        )
        channel = channels[0]
        channel["manual_tags"] = parse_json_list(channel.get("manual_tags"))
        return {
            "channel": channel,
            "snapshots": snapshots,
            "videos": videos,
            "peak_concurrent": peak,
            "concurrency_sample_count": sum(int(video["concurrency_samples"] or 0) for video in videos),
        }

    def set_owned_channel(self, channel_id: str) -> dict[str, Any]:
        rows = self.database.rows(
            """SELECT channel_id,title FROM channels
               WHERE channel_id=? AND discovery_status IN ('eligible','owned')""",
            (channel_id,),
        )
        if not rows:
            raise ValueError("請先搜尋並加入自己的頻道")
        self.database.add_workspace_channel(channel_id)
        self.database.set_settings({"owned_channel_id": channel_id})
        return rows[0]

    def owned_channel_id(self) -> str | None:
        value = self.database.get_setting("owned_channel_id", "")
        return str(value).strip() or None

    def workspace_channel_ids(self) -> list[str]:
        return self.database.workspace_channel_ids()

    def remove_workspace_channel(self, channel_id: str) -> dict[str, Any]:
        rows = self.database.rows(
            "SELECT channel_id,title FROM channels WHERE channel_id=?", (channel_id,)
        )
        if not rows or not self.database.remove_workspace_channel(channel_id):
            raise ValueError("這個頻道不在目前工作區")
        remaining = self.workspace_channel_ids()
        if self.owned_channel_id() == channel_id:
            self.database.set_settings({"owned_channel_id": remaining[0] if remaining else ""})
        return {"channel_id": channel_id, "title": rows[0]["title"], "remaining": len(remaining)}

    def workspace_channels(self) -> list[dict[str, Any]]:
        rows = self.database.rows(
            """SELECT c.channel_id,c.title,c.handle,c.thumbnail_url,c.subscriber_count,
                      c.view_count,c.video_count,c.category,c.organization_name,c.manual_tags,
                      c.activity_status,c.last_stats_at,w.added_at,w.display_order
               FROM creator_workspace_channels w
               JOIN channels c ON c.channel_id=w.channel_id
               ORDER BY w.display_order,w.added_at,c.title"""
        )
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
        for row in rows:
            snapshots = self.database.rows(
                """SELECT captured_at,subscriber_count,view_count,video_count
                   FROM channel_snapshots WHERE channel_id=? AND captured_at>=?
                   ORDER BY captured_at ASC""",
                (row["channel_id"], cutoff),
            )
            baseline_rows = self.database.rows(
                """SELECT captured_at,subscriber_count,view_count,video_count
                   FROM channel_snapshots WHERE channel_id=? AND captured_at<=?
                   ORDER BY captured_at DESC LIMIT 1""",
                (row["channel_id"], cutoff),
            )
            baseline = baseline_rows[0] if baseline_rows else None
            for field in ("subscriber_count", "view_count", "video_count"):
                current = row.get(field)
                previous = baseline.get(field) if baseline else None
                row[f"{field}_delta_30d"] = (
                    int(current) - int(previous)
                    if current is not None and previous is not None else None
                )
            row["snapshot_count_30d"] = len(snapshots)
            row["month_ready"] = baseline is not None
            row["recent_content_count_30d"] = int(self.database.scalar(
                """SELECT COUNT(*) FROM videos WHERE channel_id=?
                   AND datetime(COALESCE(published_at,actual_start,scheduled_start,updated_at))>=datetime(?)""",
                (row["channel_id"], cutoff),
            ) or 0)
            row["peak_concurrent"] = self.database.scalar(
                """SELECT MAX(cs.concurrent_viewers) FROM concurrency_samples cs
                   JOIN videos v ON v.video_id=cs.video_id WHERE v.channel_id=?""",
                (row["channel_id"],),
            )
            row["concurrency_sample_count"] = int(self.database.scalar(
                """SELECT COUNT(*) FROM concurrency_samples cs
                   JOIN videos v ON v.video_id=cs.video_id WHERE v.channel_id=?""",
                (row["channel_id"],),
            ) or 0)
            row["manual_tags"] = parse_json_list(row.get("manual_tags"))
        return rows

    def creator_channel_id(self, payload: dict[str, Any] | None = None) -> str:
        requested = str((payload or {}).get("channel_id", "")).strip()
        channel_id = requested or self.owned_channel_id() or ""
        if not channel_id:
            raise ValueError("請先選擇管理頻道")
        if channel_id not in self.workspace_channel_ids():
            raise ValueError("這個頻道不在目前工作區")
        return channel_id

    def preview_creator_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, reports = decode_creator_upload(payload)
        rows = [row for report in reports for row in report["rows"]]
        dates = [row["event_date"] for row in rows if row["event_date"]]
        metrics = sorted({
            metric for report in reports for metric in report["recognized_metrics"]
        })
        dimensions = sorted({
            dimension
            for report in reports
            for dimension in report.get("recognized_dimensions", [])
        })
        ignored_headers = sorted({
            header
            for report in reports
            for header in report.get("ignored_headers", [])
        })
        return {
            "filename": str(payload.get("filename", ""))[:240],
            "report_count": len(reports),
            "reports": [{
                "report_name": report["report_name"],
                "row_count": len(report["rows"]),
                "recognized_metrics": report["recognized_metrics"],
                "recognized_dimensions": report.get("recognized_dimensions", []),
                "ignored_headers": report.get("ignored_headers", []),
            } for report in reports],
            "row_count": len(rows),
            "recognized_metrics": metrics,
            "recognized_dimensions": dimensions,
            "recognized_column_count": len(metrics) + len(dimensions),
            "ignored_headers": ignored_headers,
            "date_start": min(dates) if dates else None,
            "date_end": max(dates) if dates else None,
            "sample_rows": rows[:3],
        }

    def import_creator_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = self.creator_channel_id(payload)
        raw, reports = decode_creator_upload(payload)
        file_hash = hashlib.sha256(raw).hexdigest()
        return self.database.creator_import_batch(
            channel_id, str(payload.get("filename", ""))[:240], file_hash, reports
        )

    def add_creator_manual_metric(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = self.creator_channel_id(payload)
        metric_date = str(payload.get("metric_date", "")).strip()
        try:
            datetime.strptime(metric_date, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("日期必須是 YYYY-MM-DD") from error
        metric_name = str(payload.get("metric_name", "")).strip()
        if metric_name not in CREATOR_METRICS:
            raise ValueError("不支援這個補充指標")
        try:
            metric_value = float(payload.get("metric_value"))
        except (TypeError, ValueError) as error:
            raise ValueError("指標值必須是數字") from error
        video_id = str(payload.get("video_id", "")).strip()[:100]
        note = str(payload.get("note", "")).strip()[:300]
        return self.database.upsert_creator_manual_metric(
            channel_id, metric_date, video_id, metric_name, metric_value, note
        )

    def creator_oauth_status(self) -> dict[str, Any]:
        credential_status = self.oauth.status()
        connections = self.database.rows(
            """SELECT o.channel_id,o.connected_at,o.last_sync_at,o.last_data_date,o.last_error,
                      c.title,c.thumbnail_url
                 FROM creator_oauth_connections o
                 JOIN channels c ON c.channel_id=o.channel_id
                ORDER BY o.connected_at DESC LIMIT 1"""
        )
        connection = connections[0] if connections else None
        raw_error = credential_status.get("credential_error") or (
            connection["last_error"] if connection else ""
        )
        with self.state_lock:
            syncing = self.current_job == "oauth-analytics-sync"
            sync_started_at = self.current_job_started_at if syncing else None
        return {
            **credential_status,
            "connected": bool(connection and credential_status["authorized"]),
            "channel": ({
                "channel_id": connection["channel_id"],
                "title": connection["title"],
                "thumbnail_url": connection["thumbnail_url"],
            } if connection else None),
            "connected_at": connection["connected_at"] if connection else None,
            "last_sync_at": connection["last_sync_at"] if connection else None,
            "last_data_date": connection["last_data_date"] if connection else None,
            "syncing": syncing,
            "sync_started_at": sync_started_at,
            "last_error": raw_error,
            "last_error_help": oauth_error_guidance(raw_error) if raw_error else None,
            "required_scopes": list(REQUIRED_SCOPES),
            "storage": "Windows DPAPI",
        }

    def configure_creator_oauth(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.oauth.status()["authorized"]:
            raise ValueError("請先中斷目前頻道連線，再更換 OAuth 設定")
        content = str(payload.get("content_base64", "")).strip()
        try:
            raw = base64.b64decode(content, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("OAuth JSON 內容不是有效的 Base64") from error
        self.oauth.configure_client(raw)
        return self.creator_oauth_status()

    def creator_oauth_authorization_url(self) -> str:
        return self.oauth.authorization_url()

    def complete_creator_oauth(self, code: str, state: str) -> dict[str, Any]:
        item = self.oauth.complete_authorization(code, state)
        channel_id = str(item.get("id") or "").strip()
        if not channel_id:
            raise ValueError("Google 沒有回傳頻道 ID")
        self.database.restore_excluded(channel_id)
        self.database.upsert_channel(item, status="owned")
        self.database.add_workspace_channel(channel_id)
        self.database.set_settings({"owned_channel_id": channel_id})
        now = utc_now()
        self.database.execute(
            """INSERT INTO creator_oauth_connections
               (channel_id,connected_at,last_sync_at,last_data_date,last_error)
               VALUES (?,?,NULL,NULL,'')
               ON CONFLICT(channel_id) DO UPDATE SET connected_at=excluded.connected_at,
                 last_error=''""",
            (channel_id, now),
        )
        warning = ""
        try:
            self.sync_creator_oauth()
        except Exception as error:
            warning = str(error)
        return {"channel_id": channel_id, "title": item.get("snippet", {}).get("title"), "warning": warning}

    @staticmethod
    def _analytics_rows(payload: dict[str, Any], dimension: str) -> dict[str, dict[str, float]]:
        headers = [
            str(header.get("name") or "")
            for header in payload.get("columnHeaders", [])
            if isinstance(header, dict)
        ]
        output: dict[str, dict[str, float]] = {}
        for values in payload.get("rows") or []:
            if not isinstance(values, list) or len(values) != len(headers):
                continue
            row = dict(zip(headers, values))
            key = str(row.pop(dimension, "summary")) if dimension else "summary"
            metrics: dict[str, float] = {}
            for name, raw in row.items():
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                mapped = {
                    "estimatedMinutesWatched": "watch_time_hours",
                    "averageViewDuration": "average_view_duration_seconds",
                    "averageViewPercentage": "average_percentage_viewed",
                    "subscribersGained": "subscribers_gained",
                    "subscribersLost": "subscribers_lost",
                    "engagedViews": "engaged_views",
                    "uniques": "unique_viewers",
                }.get(name, name)
                metrics[mapped] = value / 60 if name == "estimatedMinutesWatched" else value
            gained = metrics.get("subscribers_gained")
            lost = metrics.get("subscribers_lost")
            if gained is not None or lost is not None:
                metrics["subscribers_net"] = (gained or 0) - (lost or 0)
            output[key] = metrics
        return output

    def _fetch_creator_analytics(
        self, start_date: str, end_date: str, dimension: str = "",
    ) -> dict[str, dict[str, float]]:
        options: dict[str, Any] = {}
        if dimension == "day":
            options = {"dimensions": "day", "sort": "day"}
        elif dimension == "video":
            options = {"dimensions": "video", "sort": "-views", "max_results": 200}
        core = self.oauth.analytics_report(
            start_date,
            end_date,
            "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost",
            **options,
        )
        merged = self._analytics_rows(core, dimension)
        optional_metrics = ("engagedViews", "likes", "comments", "shares", "uniques")
        try:
            optional = self.oauth.analytics_report(
                start_date, end_date, ",".join(optional_metrics), **options,
            )
            optional_results = [self._analytics_rows(optional, dimension)]
        except OAuthError:
            optional_results = []
            for metric in optional_metrics:
                try:
                    result = self.oauth.analytics_report(start_date, end_date, metric, **options)
                except OAuthError:
                    continue
                optional_results.append(self._analytics_rows(result, dimension))
        for result in optional_results:
            for key, metrics in result.items():
                merged.setdefault(key, {}).update(metrics)
        return merged

    def sync_creator_oauth(self) -> dict[str, Any]:
        connections = self.database.rows(
            "SELECT channel_id FROM creator_oauth_connections ORDER BY connected_at DESC LIMIT 1"
        )
        if not connections or not self.oauth.status()["authorized"]:
            raise ValueError("尚未連結 YouTube 頻道")
        channel_id = str(connections[0]["channel_id"])
        self.database.execute(
            "UPDATE creator_oauth_connections SET last_error='' WHERE channel_id=?",
            (channel_id,),
        )
        end = datetime.now(PACIFIC).date() - timedelta(days=1)
        configured_retention = int(self.settings_payload()["creator_retention_days"])
        history_days = min(365, configured_retention) if configured_retention > 0 else 365
        start = end - timedelta(days=history_days - 1)
        try:
            summary = self._fetch_creator_analytics(start.isoformat(), end.isoformat())
            daily = self._fetch_creator_analytics(start.isoformat(), end.isoformat(), "day")
            videos = self._fetch_creator_analytics(start.isoformat(), end.isoformat(), "video")
            video_details = self.oauth.video_details(list(videos))
        except Exception as error:
            self.database.execute(
                "UPDATE creator_oauth_connections SET last_error=? WHERE channel_id=?",
                (str(error)[:500], channel_id),
            )
            raise
        video_metadata: dict[str, dict[str, str | None]] = {}
        for item in video_details:
            video_id = str(item.get("id") or "").strip()
            if not video_id:
                continue
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            live = item.get("liveStreamingDetails") if isinstance(item.get("liveStreamingDetails"), dict) else {}
            thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
            thumbnail = thumbnails.get("medium") or thumbnails.get("high") or thumbnails.get("default") or {}
            video_metadata[video_id] = {
                "title": str(snippet.get("title") or "").strip() or None,
                "thumbnail_url": str(thumbnail.get("url") or "").strip() or None,
                "published_at": str(snippet.get("publishedAt") or "").strip() or None,
                "live_at": str(live.get("actualStartTime") or live.get("scheduledStartTime") or "").strip() or None,
            }
        synced_at = utc_now()
        last_data_date = max(daily) if daily else None
        with self.database.lock:
            connection = self.database.connection
            connection.execute("DELETE FROM creator_oauth_daily_metrics WHERE channel_id=?", (channel_id,))
            connection.execute("DELETE FROM creator_oauth_video_metrics WHERE channel_id=?", (channel_id,))
            connection.execute(
                """INSERT INTO creator_oauth_summary_metrics
                   (channel_id,date_start,date_end,metrics_json,synced_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(channel_id) DO UPDATE SET date_start=excluded.date_start,
                     date_end=excluded.date_end,metrics_json=excluded.metrics_json,
                     synced_at=excluded.synced_at""",
                (channel_id, start.isoformat(), end.isoformat(),
                 json.dumps(summary.get("summary", {}), ensure_ascii=False), synced_at),
            )
            connection.executemany(
                """INSERT INTO creator_oauth_daily_metrics
                   (channel_id,event_date,metrics_json,synced_at) VALUES (?,?,?,?)""",
                [(channel_id, key, json.dumps(value, ensure_ascii=False), synced_at)
                 for key, value in daily.items()],
            )
            connection.executemany(
                """INSERT INTO creator_oauth_video_metrics
                   (channel_id,video_id,title,thumbnail_url,published_at,live_at,metrics_json,synced_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [(
                    channel_id, key,
                    video_metadata.get(key, {}).get("title"),
                    video_metadata.get(key, {}).get("thumbnail_url"),
                    video_metadata.get(key, {}).get("published_at"),
                    video_metadata.get(key, {}).get("live_at"),
                    json.dumps(value, ensure_ascii=False), synced_at,
                ) for key, value in videos.items()],
            )
            connection.execute(
                """UPDATE creator_oauth_connections
                   SET last_sync_at=?,last_data_date=?,last_error='' WHERE channel_id=?""",
                (synced_at, last_data_date, channel_id),
            )
            connection.commit()
        return {
            "channel_id": channel_id,
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "daily_rows": len(daily),
            "video_rows": len(videos),
            "video_metadata_rows": len(video_metadata),
            "synced_at": synced_at,
        }

    def disconnect_creator_oauth(self) -> dict[str, Any]:
        warning = self.oauth.revoke_and_delete()
        with self.database.lock:
            connection = self.database.connection
            connection.execute("DELETE FROM creator_oauth_summary_metrics")
            connection.execute("DELETE FROM creator_oauth_daily_metrics")
            connection.execute("DELETE FROM creator_oauth_video_metrics")
            connection.execute("DELETE FROM creator_oauth_connections")
            connection.commit()
        return {"warning": warning}

    def delete_creator_oauth_client(self) -> None:
        self.oauth.delete_client()

    def creator_oauth_data(self, channel_id: str | None) -> dict[str, Any]:
        status = self.creator_oauth_status()
        if not channel_id or not status["connected"] or status["channel"]["channel_id"] != channel_id:
            return {"status": status, "summary": None, "daily": [], "videos": []}
        summary_rows = self.database.rows(
            "SELECT date_start,date_end,metrics_json,synced_at FROM creator_oauth_summary_metrics WHERE channel_id=?",
            (channel_id,),
        )
        daily_rows = self.database.rows(
            """SELECT event_date,metrics_json,synced_at FROM creator_oauth_daily_metrics
               WHERE channel_id=? ORDER BY event_date DESC LIMIT 90""",
            (channel_id,),
        )
        video_rows = self.database.rows(
            """SELECT o.video_id,o.metrics_json,o.synced_at,
                      COALESCE(o.title,v.title) AS title,
                      COALESCE(o.thumbnail_url,v.thumbnail_url) AS thumbnail_url,
                      COALESCE(o.published_at,v.published_at) AS published_at,
                      o.live_at,
                      COALESCE(o.live_at,o.published_at,v.actual_start,v.scheduled_start,v.published_at)
                        AS content_date
                 FROM creator_oauth_video_metrics o
                 LEFT JOIN videos v ON v.video_id=o.video_id AND v.channel_id=o.channel_id
                WHERE o.channel_id=? ORDER BY json_extract(o.metrics_json,'$.views') DESC LIMIT 50""",
            (channel_id,),
        )
        summary = None
        if summary_rows:
            summary = summary_rows[0]
            summary["metrics"] = json.loads(summary.pop("metrics_json"))
        for row in daily_rows + video_rows:
            row["metrics"] = json.loads(row.pop("metrics_json"))
        return {"status": status, "summary": summary, "daily": daily_rows, "videos": video_rows}

    def creator_dashboard(self, requested_channel_id: str | None = None) -> dict[str, Any]:
        workspace_channels = self.workspace_channels()
        workspace_ids = {row["channel_id"] for row in workspace_channels}
        channel_id = (requested_channel_id or "").strip() or self.owned_channel_id()
        if channel_id and channel_id not in workspace_ids:
            channel_id = None
        if not channel_id and workspace_channels:
            channel_id = workspace_channels[0]["channel_id"]
        if not channel_id:
            return {
                "owned_channel_id": None,
                "workspace_channels": workspace_channels,
                "channel": None,
                "public": None,
                "oauth": self.creator_oauth_data(None),
                "imports": [],
                "imported_overview": {},
                "overview_sources": {},
                "recent_rows": [],
                "manual_metrics": [],
            }
        try:
            detail = self.channel_detail(channel_id)
        except ValueError:
            return {
                "owned_channel_id": channel_id,
                "workspace_channels": workspace_channels,
                "channel": None,
                "public": None,
                "oauth": self.creator_oauth_data(None),
                "imports": [],
                "imported_overview": {},
                "overview_sources": {},
                "recent_rows": [],
                "manual_metrics": [],
            }
        imports = self.database.rows(
            """SELECT id,filename,imported_at,report_count,row_count,inserted_count,
                      duplicate_count,conflict_count,date_start,date_end
               FROM creator_import_batches WHERE channel_id=?
               ORDER BY id DESC LIMIT 50""",
            (channel_id,),
        )
        analytics_rows = self.database.rows(
            f"""SELECT * FROM creator_analytics_rows r
                 WHERE channel_id=? AND id=(
                   SELECT MAX(id) FROM creator_analytics_rows latest
                   WHERE latest.channel_id=r.channel_id AND latest.natural_key=r.natural_key
                 ) ORDER BY id DESC""",
            (channel_id,),
        )
        imported_overview: dict[str, float | None] = {}
        overview_sources: dict[str, int] = {}
        for metric in CREATOR_METRICS:
            candidates = [row for row in analytics_rows if row.get(metric) is not None]
            if not candidates:
                imported_overview[metric] = None
                continue
            total_rows = [row for row in candidates if row.get("row_kind") == "total"]
            if total_rows:
                latest_batch = max(int(row["batch_id"]) for row in total_rows)
                chosen_total = max(
                    (
                        row for row in total_rows
                        if int(row["batch_id"]) == latest_batch
                    ),
                    key=lambda row: int(row["id"]),
                )
                imported_overview[metric] = float(chosen_total[metric])
                overview_sources[metric] = latest_batch
                continue
            groups: dict[tuple[bool, bool, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
            for row in candidates:
                try:
                    dimension_keys = tuple(sorted(json.loads(row.get("dimensions_json") or "{}").keys()))
                except (AttributeError, json.JSONDecodeError):
                    dimension_keys = ()
                groups[(bool(row.get("event_date")), bool(row.get("video_id")), dimension_keys)].append(row)
            chosen = max(
                groups.values(),
                key=lambda group: (max(int(row["batch_id"]) for row in group), len(group)),
            )
            latest_batch = max(int(row["batch_id"]) for row in chosen)
            values = [float(row[metric]) for row in chosen if row.get(metric) is not None]
            if metric in CREATOR_AVERAGE_METRICS:
                imported_overview[metric] = sum(values) / len(values) if values else None
            else:
                imported_overview[metric] = sum(values) if values else None
            overview_sources[metric] = latest_batch
        recent_rows = analytics_rows[:100]
        for row in recent_rows:
            row["dimensions"] = json.loads(row.pop("dimensions_json") or "{}")
        manual_metrics = self.database.rows(
            """SELECT id,metric_date,video_id,metric_name,metric_value,note,created_at,updated_at
               FROM creator_manual_metrics WHERE channel_id=?
               ORDER BY metric_date DESC,id DESC LIMIT 200""",
            (channel_id,),
        )
        return {
            "owned_channel_id": channel_id,
            "workspace_channels": workspace_channels,
            "channel": detail["channel"],
            "public": {
                "snapshots": detail["snapshots"],
                "videos": detail["videos"],
                "peak_concurrent": detail["peak_concurrent"],
                "concurrency_sample_count": detail["concurrency_sample_count"],
            },
            "oauth": self.creator_oauth_data(channel_id),
            "imports": imports,
            "imported_overview": imported_overview,
            "overview_sources": overview_sources,
            "recent_rows": recent_rows,
            "manual_metrics": manual_metrics,
        }

    def _reclassify_automatic_videos(self) -> None:
        confirmed_game_names = [
            row["game_name"] for row in self.database.rows(
                "SELECT DISTINCT game_name FROM video_classification_overrides WHERE game_name<>''"
            )
        ]
        videos = self.database.rows(
            """SELECT v.video_id,v.title,v.description,v.category_id,v.tags,v.live_state
                 FROM videos v LEFT JOIN video_classification_overrides o ON o.video_id=v.video_id
                WHERE o.video_id IS NULL"""
        )
        rows: list[tuple[str, str, str, str, str, str]] = []
        for video in videos:
            details = classify_content_details(
                video["title"], video["description"] or "", parse_json_list(video["tags"]),
                video["category_id"], video["live_state"], confirmed_game_names
            )
            rows.append((
                details["content_type"], json.dumps(details["labels"], ensure_ascii=False),
                details["classification_source"], details["classification_evidence"],
                details["game_name"], video["video_id"],
            ))
        if rows:
            self.database.executemany(
                """UPDATE videos SET content_type=?,content_tags=?,classification_source=?,
                          classification_evidence=?,game_name=? WHERE video_id=?""",
                rows,
            )

    def update_video_classification(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_id = str(payload.get("video_id", "")).strip()
        if not video_id:
            raise ValueError("缺少 video_id")
        video_rows = self.database.rows(
            "SELECT video_id,title,content_tags FROM videos WHERE video_id=?", (video_id,)
        )
        if not video_rows:
            raise ValueError("找不到這筆內容")
        raw_topics = payload.get("topics", [])
        if not isinstance(raw_topics, list):
            raise ValueError("topics 必須是陣列")
        topics = [
            label for label in CONTENT_TOPIC_ORDER
            if label in {str(value).strip() for value in raw_topics} and label != "其他"
        ]
        if not topics:
            topics = ["其他"]
        game_name = str(payload.get("game_name", "")).strip()[:120]
        if game_name and "遊戲" not in topics:
            if topics == ["其他"]:
                topics = []
            topics.append("遊戲")
            topics.sort(key=CONTENT_TOPIC_ORDER.index)
        note = str(payload.get("note", "")).strip()[:240]
        evidence = note or f"使用者已確認：{' + '.join(topics)}"
        if game_name:
            evidence += f"；遊戲：{game_name}"
        attributes = [
            label for label in parse_json_list(video_rows[0]["content_tags"])
            if label in CONTENT_ATTRIBUTES
        ]
        now = utc_now()
        self.database.execute(
            """INSERT INTO video_classification_overrides(video_id,content_topics,game_name,note,updated_at)
               VALUES (?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET
                 content_topics=excluded.content_topics,game_name=excluded.game_name,
                 note=excluded.note,updated_at=excluded.updated_at""",
            (video_id, json.dumps(topics, ensure_ascii=False), game_name, note, now),
        )
        self.database.execute(
            """UPDATE videos SET content_type=?,content_tags=?,classification_source='人工確認',
                      classification_evidence=?,game_name=? WHERE video_id=?""",
            (
                " + ".join(topics), json.dumps([*topics, *attributes], ensure_ascii=False),
                evidence, game_name, video_id,
            ),
        )
        self._reclassify_automatic_videos()
        return self.database.rows(
            """SELECT video_id,title,content_type,content_tags,classification_source,
                      classification_evidence,game_name FROM videos WHERE video_id=?""",
            (video_id,),
        )[0]

    def reset_video_classification(self, video_id: str) -> dict[str, Any]:
        normalized = video_id.strip()
        if not normalized or not self.database.scalar(
            "SELECT 1 FROM video_classification_overrides WHERE video_id=?", (normalized,)
        ):
            raise ValueError("找不到這筆人工分類")
        self.database.execute(
            "DELETE FROM video_classification_overrides WHERE video_id=?", (normalized,)
        )
        self._reclassify_automatic_videos()
        return self.database.rows(
            """SELECT video_id,title,content_type,content_tags,classification_source,
                      classification_evidence,game_name FROM videos WHERE video_id=?""",
            (normalized,),
        )[0]

    def creator_analytics(
        self,
        requested_channel_id: str | None = None,
        *,
        query: str = "",
        date_start: str = "",
        date_end: str = "",
        report_name: str = "all",
        row_kind: str = "detail",
        status: str = "all",
        content_format: str = "all",
        content_topic: str = "all",
        sort: str = "date",
        direction: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        workspace_ids = set(self.workspace_channel_ids())
        channel_id = (requested_channel_id or "").strip() or self.owned_channel_id()
        if not channel_id or channel_id not in workspace_ids:
            return {
                "channel_id": None,
                "rows": [],
                "result_count": 0,
                "available_count": 0,
                "page": 1,
                "page_size": 50,
                "page_count": 0,
                "reports": [],
                "content_formats": [],
                "content_topics": [],
            }

        for label, value in (("開始日期", date_start), ("結束日期", date_end)):
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError as error:
                    raise ValueError(f"{label}必須是 YYYY-MM-DD") from error
        if date_start and date_end and date_start > date_end:
            raise ValueError("開始日期不可晚於結束日期")

        allowed_row_kinds = {"all", "detail", "total"}
        allowed_statuses = {"all", "normal", "conflict"}
        allowed_sorts = {
            "date": "display_date",
            "content": "video_title",
            "report_name": "report_name",
            "content_format": "content_format",
            "content_topic": "content_topic",
            "views": "views",
            "engaged_views": "engaged_views",
            "watch_time_hours": "watch_time_hours",
            "average_view_duration_seconds": "average_view_duration_seconds",
            "average_percentage_viewed": "average_percentage_viewed",
            "impressions": "impressions",
            "impressions_ctr": "impressions_ctr",
            "unique_viewers": "unique_viewers",
            "returning_viewers": "returning_viewers",
            "likes": "likes",
            "comments": "comments",
            "conflict_status": "conflict_status",
        }
        if row_kind not in allowed_row_kinds:
            raise ValueError("不支援這個資料列類型")
        if status not in allowed_statuses:
            raise ValueError("不支援這個核對狀態")
        if sort not in allowed_sorts:
            raise ValueError("不支援這個排序欄位")
        if direction not in {"asc", "desc"}:
            raise ValueError("排序方向必須是 asc 或 desc")
        if page < 1:
            raise ValueError("頁碼必須大於 0")
        if page_size not in {25, 50, 100}:
            raise ValueError("每頁筆數只支援 25、50 或 100")

        analytics_rows = self.database.rows(
            """SELECT r.*,
                      v.video_id AS matched_public_video_id,
                      v.live_state AS public_live_state,
                      v.duration_seconds AS public_duration_seconds,
                      v.content_type AS public_content_type,
                      v.classification_source AS public_classification_source,
                      v.classification_evidence AS public_classification_evidence,
                      v.game_name AS public_game_name
                 FROM creator_analytics_rows r
                 LEFT JOIN videos v ON v.video_id=r.video_id AND v.channel_id=r.channel_id
                WHERE r.channel_id=? AND r.id=(
                  SELECT MAX(id) FROM creator_analytics_rows latest
                   WHERE latest.channel_id=r.channel_id AND latest.natural_key=r.natural_key
                )
                ORDER BY r.id DESC""",
            (channel_id,),
        )

        enriched_rows: list[dict[str, Any]] = []
        for row in analytics_rows:
            row["dimensions"] = json.loads(row.pop("dimensions_json") or "{}")
            row["display_date"] = row.get("event_date") or row.get("published_at")
            row["date_source"] = (
                "資料日期" if row.get("event_date") else
                "發布日" if row.get("published_at") else
                "無日期"
            )
            matched_public_video_id = row.pop("matched_public_video_id", None)
            public_live_state = row.pop("public_live_state", None)
            public_duration_seconds = row.pop("public_duration_seconds", None)
            public_content_type = row.pop("public_content_type", None)
            public_classification_source = row.pop("public_classification_source", None)
            public_classification_evidence = row.pop("public_classification_evidence", None)
            public_game_name = row.pop("public_game_name", None)
            title = str(row.get("video_title") or "")

            if matched_public_video_id:
                duration = public_duration_seconds if public_duration_seconds is not None else row.get("duration_seconds")
                row["content_format"] = video_format(
                    str(public_live_state or ""),
                    int(duration) if duration is not None else None,
                    title,
                )
                row["content_topic"] = str(public_content_type or classify_content_fields(title))
                row["classification_source"] = str(public_classification_source or "公開監測規則")
                row["classification_evidence"] = str(public_classification_evidence or "")
                row["game_name"] = str(public_game_name or "")
            else:
                duration = row.get("duration_seconds")
                row["content_format"] = "Shorts" if duration is not None and float(duration) <= 60 else "未判斷"
                inferred_topic = classify_content_fields(title)
                row["content_topic"] = inferred_topic
                row["classification_source"] = "匯入標題規則" if inferred_topic != "其他" else "未分類"
                row["classification_evidence"] = ""
                row["game_name"] = ""
            enriched_rows.append(row)

        reports = sorted({str(row["report_name"]) for row in enriched_rows}, key=str.casefold)
        content_formats = sorted({str(row["content_format"]) for row in enriched_rows}, key=str.casefold)
        content_topics = sorted({str(row["content_topic"]) for row in enriched_rows}, key=str.casefold)
        available_count = len(enriched_rows)

        search_text = query.strip().casefold()
        filtered_rows: list[dict[str, Any]] = []
        for row in enriched_rows:
            display_date = str(row.get("display_date") or "")
            if search_text and search_text not in " ".join((
                str(row.get("video_title") or ""),
                str(row.get("video_id") or ""),
                str(row.get("report_name") or ""),
                str(row.get("game_name") or ""),
            )).casefold():
                continue
            if date_start and (not display_date or display_date < date_start):
                continue
            if date_end and (not display_date or display_date > date_end):
                continue
            if report_name != "all" and row.get("report_name") != report_name:
                continue
            if row_kind != "all" and row.get("row_kind") != row_kind:
                continue
            if status == "normal" and int(row.get("conflict_status") or 0) != 0:
                continue
            if status == "conflict" and int(row.get("conflict_status") or 0) == 0:
                continue
            if content_format != "all" and row.get("content_format") != content_format:
                continue
            if content_topic != "all" and row.get("content_topic") != content_topic:
                continue
            filtered_rows.append(row)

        sort_key = allowed_sorts[sort]

        def comparable(row: dict[str, Any]) -> Any:
            value = row.get(sort_key)
            if sort == "content":
                value = value or row.get("video_id")
            return value.casefold() if isinstance(value, str) else value

        present_rows = [row for row in filtered_rows if comparable(row) is not None]
        missing_rows = [row for row in filtered_rows if comparable(row) is None]
        present_rows.sort(key=comparable, reverse=direction == "desc")
        ordered_rows = present_rows + missing_rows

        result_count = len(ordered_rows)
        page_count = (result_count + page_size - 1) // page_size
        resolved_page = min(page, page_count) if page_count else 1
        offset = (resolved_page - 1) * page_size
        return {
            "channel_id": channel_id,
            "rows": ordered_rows[offset:offset + page_size],
            "result_count": result_count,
            "available_count": available_count,
            "page": resolved_page,
            "page_size": page_size,
            "page_count": page_count,
            "reports": reports,
            "content_formats": content_formats,
            "content_topics": content_topics,
        }

    def delete_creator_import(self, batch_id: int) -> dict[str, Any]:
        channel_id = self.owned_channel_id()
        if not channel_id:
            raise ValueError("請先選擇你的頻道")
        deleted = self.database.delete_creator_import(channel_id, batch_id)
        if not deleted:
            raise ValueError("找不到這個匯入批次")
        return deleted

    def delete_creator_manual_metric(self, metric_id: int) -> None:
        channel_id = self.owned_channel_id()
        if not channel_id or not self.database.delete_creator_manual_metric(channel_id, metric_id):
            raise ValueError("找不到這筆手動補充資料")

    def insights(
        self,
        days: int = 30,
        min_subscribers: int = 0,
        max_subscribers: int = 10_000_000,
        category: str | None = None,
        reference_channel_id: str | None = None,
        include_graduated: bool = False,
        channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        days = max(1, min(365, int(days)))
        min_subscribers = max(0, int(min_subscribers))
        max_subscribers = max(min_subscribers, min(100_000_000, int(max_subscribers)))
        selected_ids = list(dict.fromkeys(channel_id for channel_id in (channel_ids or []) if channel_id))[:20]
        conditions = ["discovery_status='eligible'"]
        params: list[Any] = []
        if selected_ids:
            conditions.append(f"channel_id IN ({','.join('?' for _ in selected_ids)})")
            params.extend(selected_ids)
        else:
            conditions.append("COALESCE(subscriber_count,0) BETWEEN ? AND ?")
            params.extend([min_subscribers, max_subscribers])
        if category and category != "全部":
            conditions.append("category=?")
            params.append(category)
        if not include_graduated:
            conditions.append("activity_status<>'已確認畢業'")
        if reference_channel_id:
            conditions.append("channel_id<>?")
            params.append(reference_channel_id)
        cohort_channels = self.database.rows(
            f"""SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                       video_count,category,updated_at
                FROM channels WHERE {' AND '.join(conditions)}
                ORDER BY subscriber_count DESC""",
            tuple(params),
        )
        reference_rows = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                      video_count,category,updated_at
               FROM channels WHERE channel_id=? AND discovery_status IN ('eligible','owned')""",
            (reference_channel_id,),
        ) if reference_channel_id else []
        reference_channel = reference_rows[0] if reference_rows else None

        analysis_ids = [channel["channel_id"] for channel in cohort_channels]
        if reference_channel and reference_channel["channel_id"] not in analysis_ids:
            analysis_ids.append(reference_channel["channel_id"])
        period_end = datetime.now(TAIPEI)
        period_start = period_end - timedelta(days=days)
        cutoff = period_start.astimezone(UTC).isoformat(timespec="seconds")

        videos: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        for analysis_group in chunks(analysis_ids, 400):
            placeholders = ",".join("?" for _ in analysis_group)
            videos.extend(self.database.rows(
                    f"""SELECT v.video_id,v.channel_id,v.title,v.description,v.thumbnail_url,
                           v.published_at,v.duration_seconds,v.category_id,v.tags,v.content_type,v.content_tags,
                           v.classification_source,v.classification_evidence,v.game_name,
                           v.view_count,v.like_count,v.comment_count,v.live_state,
                           v.current_concurrent,v.scheduled_start,v.actual_start,v.actual_end,
                           MAX(cs.concurrent_viewers) AS peak_concurrent
                    FROM videos v
                    LEFT JOIN concurrency_samples cs ON cs.video_id=v.video_id
                    WHERE v.channel_id IN ({placeholders})
                      AND datetime(COALESCE(v.published_at,v.actual_start,v.scheduled_start,v.updated_at))
                          >= datetime(?)
                    GROUP BY v.video_id""",
                tuple([*analysis_group, cutoff]),
            ))
            snapshots.extend(self.database.rows(
                f"""SELECT channel_id,captured_at,subscriber_count,view_count,video_count
                    FROM channel_snapshots
                    WHERE channel_id IN ({placeholders}) AND datetime(captured_at)>=datetime(?)
                    ORDER BY captured_at ASC""",
                tuple([*analysis_group, cutoff]),
            ))

        videos_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for video in videos:
            video["format_type"] = video_format(
                video["live_state"], video["duration_seconds"], video["title"], video["description"] or ""
            )
            video["content_labels"] = parse_json_list(video.get("content_tags"))
            videos_by_channel[video["channel_id"]].append(video)

        snapshots_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for snapshot in snapshots:
            snapshots_by_channel[snapshot["channel_id"]].append(snapshot)

        def growth(channel_id: str, field: str) -> int | None:
            rows = [row for row in snapshots_by_channel[channel_id] if row[field] is not None]
            if len(rows) < 2:
                return None
            return int(rows[-1][field]) - int(rows[0][field])

        def channel_metrics(channel: dict[str, Any]) -> dict[str, Any]:
            channel_videos = videos_by_channel[channel["channel_id"]]
            subscribers = int(channel["subscriber_count"] or 0)
            views = [video["view_count"] for video in channel_videos if video["view_count"] is not None]
            view_rates = [
                float(video["view_count"]) / subscribers * 100
                for video in channel_videos if subscribers and video["view_count"] is not None
            ]
            live_videos = [video for video in channel_videos if video["format_type"] == "直播"]
            peaks = [video["peak_concurrent"] for video in live_videos if video["peak_concurrent"] is not None]
            ccv_rates = [float(peak) / subscribers * 100 for peak in peaks] if subscribers else []
            durations = [video["duration_seconds"] for video in channel_videos if video["duration_seconds"] is not None]
            return {
                "channel_id": channel["channel_id"],
                "title": channel["title"],
                "thumbnail_url": channel["thumbnail_url"],
                "category": channel["category"],
                "subscriber_count": channel["subscriber_count"],
                "items": len(channel_videos),
                "streams": len(live_videos),
                "weekly_frequency": len(channel_videos) / days * 7,
                "average_duration_seconds": sum(durations) / len(durations) if durations else None,
                "median_views": percentile(views, .5),
                "median_view_rate": percentile(view_rates, .5),
                "median_peak_concurrent": percentile(peaks, .5),
                "median_ccv_rate": percentile(ccv_rates, .5),
                "subscriber_growth": growth(channel["channel_id"], "subscriber_count"),
                "view_growth": growth(channel["channel_id"], "view_count"),
                "snapshot_count": len(snapshots_by_channel[channel["channel_id"]]),
            }

        cohort_metrics = [channel_metrics(channel) for channel in cohort_channels]
        reference_metrics = channel_metrics(reference_channel) if reference_channel else None
        benchmark_fields = (
            "weekly_frequency", "average_duration_seconds", "median_views",
            "median_view_rate", "median_peak_concurrent", "median_ccv_rate",
            "subscriber_growth", "view_growth",
        )
        benchmarks = {
            field: {
                "median": percentile((row[field] for row in cohort_metrics), .5),
                "p75": percentile((row[field] for row in cohort_metrics), .75),
            }
            for field in benchmark_fields
        }

        cohort_ids = {channel["channel_id"] for channel in cohort_channels}
        cohort_videos = [video for video in videos if video["channel_id"] in cohort_ids]
        channel_lookup = {channel["channel_id"]: channel for channel in cohort_channels}
        format_counter: Counter[str] = Counter()
        keyword_counter: Counter[str] = Counter()
        schedule_counts = [[0 for _ in range(6)] for _ in range(7)]
        schedule_peaks: list[list[list[int]]] = [[[] for _ in range(6)] for _ in range(7)]
        schedule_streams: list[list[list[dict[str, Any]]]] = [[[] for _ in range(6)] for _ in range(7)]
        ranked_videos: list[dict[str, Any]] = []

        for video in cohort_videos:
            format_counter[video["format_type"]] += 1
            keyword_counter.update(extract_video_keywords(video["title"], video["tags"]))
            channel = channel_lookup.get(video["channel_id"], {})
            subscribers = int(channel.get("subscriber_count") or 0)
            view_rate = (
                float(video["view_count"]) / subscribers * 100
                if subscribers and video["view_count"] is not None else None
            )
            ranked_videos.append({
                "video_id": video["video_id"], "title": video["title"],
                "thumbnail_url": video["thumbnail_url"], "channel_id": video["channel_id"],
                "channel_title": channel.get("title", video["channel_id"]),
                "subscriber_count": channel.get("subscriber_count"),
                "view_count": video["view_count"], "view_rate": view_rate,
                "peak_concurrent": video["peak_concurrent"],
                "ccv_rate": (
                    float(video["peak_concurrent"]) / subscribers * 100
                    if subscribers and video["peak_concurrent"] is not None else None
                ),
                "content_type": video["content_type"] or "其他",
                "topics": [label for label in video["content_labels"] if label in CONTENT_TOPIC_ORDER],
                "attributes": [label for label in video["content_labels"] if label in CONTENT_ATTRIBUTES],
                "classification_source": video["classification_source"] or "未辨識",
                "classification_evidence": video["classification_evidence"] or "",
                "game_name": video["game_name"] or "",
                "format_type": video["format_type"], "published_at": video["published_at"],
            })
            if video["format_type"] == "直播":
                started = parse_api_time(video["actual_start"] or video["scheduled_start"] or video["published_at"])
                if started:
                    local_time = started.astimezone(TAIPEI)
                    day = local_time.weekday()
                    block = local_time.hour // 4
                    schedule_counts[day][block] += 1
                    if video["peak_concurrent"] is not None:
                        schedule_peaks[day][block].append(int(video["peak_concurrent"]))
                    schedule_streams[day][block].append({
                        "video_id": video["video_id"],
                        "title": video["title"],
                        "channel_id": video["channel_id"],
                        "channel_title": channel.get("title", video["channel_id"]),
                        "started_at": local_time.isoformat(timespec="minutes"),
                        "peak_concurrent": video["peak_concurrent"],
                    })

        def ranked_distinct(
            candidates: Iterable[dict[str, Any]],
            metric: str,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            ordered = sorted(
                candidates,
                key=lambda row: row.get(metric) if row.get(metric) is not None else -1,
                reverse=True,
            )
            result: list[dict[str, Any]] = []
            seen_channels: set[str] = set()
            for row in ordered:
                if row["channel_id"] in seen_channels:
                    continue
                result.append(row)
                seen_channels.add(row["channel_id"])
                if len(result) >= limit:
                    break
            return result

        def breakdown_item(
            label: str,
            items: list[dict[str, Any]],
            total_items: int,
            attribute: bool = False,
        ) -> dict[str, Any]:
            views = [item["view_count"] for item in items if item["view_count"] is not None]
            rates: list[float] = []
            peaks = [item["peak_concurrent"] for item in items if item["peak_concurrent"] is not None]
            durations = [item["duration_seconds"] for item in items if item["duration_seconds"] is not None]
            for item in items:
                subscribers = int(channel_lookup.get(item["channel_id"], {}).get("subscriber_count") or 0)
                if subscribers and item["view_count"] is not None:
                    rates.append(float(item["view_count"]) / subscribers * 100)
            video_ids = {item["video_id"] for item in items}
            topic_labels = [part.strip() for part in label.split(" + ") if part.strip()]
            if len(topic_labels) > 1:
                description = "同一內容同時包含" + "與".join(topic_labels)
            else:
                description = CONTENT_TYPE_DESCRIPTIONS.get(label, CONTENT_TYPE_DESCRIPTIONS["其他"])
            return {
                "content_type": label,
                "description": description,
                "items": len(items),
                "share": len(items) / total_items * 100 if total_items else 0,
                "streams": sum(1 for item in items if item["format_type"] == "直播"),
                "median_views": percentile(views, .5),
                "median_view_rate": percentile(rates, .5),
                "median_peak_concurrent": percentile(peaks, .5),
                "average_duration_seconds": sum(durations) / len(durations) if durations else None,
                "representative_videos": ranked_distinct(
                    (row for row in ranked_videos if row["video_id"] in video_ids),
                    "view_rate",
                    5,
                ),
                "is_attribute": attribute,
            }

        def build_landscape(source: list[dict[str, Any]]) -> dict[str, Any]:
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            collaboration_items: list[dict[str, Any]] = []
            for item in source:
                groups[item["content_type"] or "其他"].append(item)
                if "聯動" in item["content_labels"]:
                    collaboration_items.append(item)
            breakdown = [
                breakdown_item(label, items, len(source)) for label, items in groups.items()
            ]
            breakdown.sort(key=lambda row: row["items"], reverse=True)
            return {
                "items": len(source),
                "content_breakdown": breakdown,
                "collaboration": breakdown_item("聯動", collaboration_items, len(source), True),
            }

        content_landscapes = {
            "主要內容": build_landscape([video for video in cohort_videos if video["format_type"] != "Shorts"]),
            "直播": build_landscape([video for video in cohort_videos if video["format_type"] == "直播"]),
            "影片": build_landscape([video for video in cohort_videos if video["format_type"] == "影片"]),
            "Shorts": build_landscape([video for video in cohort_videos if video["format_type"] == "Shorts"]),
            "全部": build_landscape(cohort_videos),
        }
        content_breakdown = content_landscapes["全部"]["content_breakdown"]

        schedule = [
            [
                {
                    "count": schedule_counts[day][block],
                    "median_peak": percentile(schedule_peaks[day][block], .5),
                    "streams": sorted(
                        schedule_streams[day][block],
                        key=lambda row: (row["started_at"], row["channel_title"].casefold()),
                    ),
                }
                for block in range(6)
            ]
            for day in range(7)
        ]
        schedule_stream_count = sum(sum(row) for row in schedule_counts)
        schedule_channel_count = len({
            stream["channel_id"]
            for day in schedule_streams for block in day for stream in block
        })
        day_labels = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")
        block_labels = ("00:00–04:00", "04:00–08:00", "08:00–12:00", "12:00–16:00", "16:00–20:00", "20:00–24:00")
        if schedule_stream_count:
            busiest_day, busiest_block = max(
                ((day, block) for day in range(7) for block in range(6)),
                key=lambda pair: schedule_counts[pair[0]][pair[1]],
            )
            busiest_count = schedule_counts[busiest_day][busiest_block]
            schedule_conclusion = (
                f"開台最集中在{day_labels[busiest_day]} {block_labels[busiest_block]}，"
                f"共 {busiest_count} 場，占有時間資料直播的 "
                f"{busiest_count / schedule_stream_count * 100:.1f}%。"
            )
            if schedule_stream_count < 10:
                schedule_conclusion += "目前樣本較少，建議累積更多直播後再判讀固定時段。"
        else:
            schedule_conclusion = "目前期間沒有可用的直播開始時間，尚無法判斷集中時段。"
        ranked_videos.sort(key=lambda row: row["view_rate"] if row["view_rate"] is not None else -1, reverse=True)
        videos_by_format = {
            "綜合": ranked_distinct(ranked_videos, "view_rate", 12),
            "影片": ranked_distinct(
                (row for row in ranked_videos if row["format_type"] == "影片"), "view_rate", 12
            ),
            "直播": ranked_distinct(
                (row for row in ranked_videos if row["format_type"] == "直播"), "ccv_rate", 12
            ),
            "Shorts": ranked_distinct(
                (row for row in ranked_videos if row["format_type"] == "Shorts"), "view_rate", 12
            ),
        }
        growth_covered = sum(1 for row in cohort_metrics if row["snapshot_count"] >= 2)
        classified = sum(1 for video in cohort_videos if video["content_type"] != "其他")
        live_seconds = sum(
            int(video["duration_seconds"] or 0) for video in cohort_videos if video["format_type"] == "直播"
        )

        return {
            "generated_at": utc_now(),
            "period_days": days,
            "filters": {
                "min_subscribers": min_subscribers,
                "max_subscribers": max_subscribers,
                "category": category or "全部",
                "include_graduated": include_graduated,
                "channel_ids": selected_ids,
            },
            "overview": {
                "channels": len(cohort_channels),
                "active_channels": len({video["channel_id"] for video in cohort_videos}),
                "total_subscribers": sum(int(channel["subscriber_count"] or 0) for channel in cohort_channels),
                "total_channel_views": sum(int(channel["view_count"] or 0) for channel in cohort_channels),
                "recent_items": len(cohort_videos),
                "recent_streams": format_counter["直播"],
                "recent_shorts": format_counter["Shorts"],
                "live_hours": live_seconds / 3600,
                "subscriber_growth": sum(int(row["subscriber_growth"] or 0) for row in cohort_metrics),
                "view_growth": sum(int(row["view_growth"] or 0) for row in cohort_metrics),
            },
            "benchmarks": benchmarks,
            "reference": reference_metrics,
            "content_breakdown": content_breakdown,
            "content_landscapes": content_landscapes,
            "format_breakdown": [
                {"format_type": label, "items": count} for label, count in format_counter.most_common()
            ],
            "schedule": schedule,
            "schedule_summary": {
                "date_start": period_start.date().isoformat(),
                "date_end": period_end.date().isoformat(),
                "channel_count": schedule_channel_count,
                "stream_count": schedule_stream_count,
                "conclusion": schedule_conclusion,
            },
            "classification_guide": {
                "priority": "人工確認 → 標題（含系統與既有確認的遊戲別名）→ YouTube 類別 → 影片標籤 → 說明文字",
                "representative_ranking": "代表內容依觀看／訂閱比由高至低排序，每個頻道最多一項；直播同接只作為卡片補充指標。",
            },
            "top_videos": videos_by_format["綜合"],
            "top_videos_by_format": videos_by_format,
            "top_channels": sorted(
                cohort_metrics,
                key=lambda row: row["median_view_rate"] if row["median_view_rate"] is not None else -1,
                reverse=True,
            )[:12],
            "keywords": [
                {"keyword": keyword, "count": count} for keyword, count in keyword_counter.most_common(18)
            ],
            "coverage": {
                "growth_channels": growth_covered,
                "growth_percent": growth_covered / len(cohort_channels) * 100 if cohort_channels else 0,
                "classified_items": classified,
                "classification_percent": classified / len(cohort_videos) * 100 if cohort_videos else 0,
            },
        }

    def trends(
        self,
        days: int = 7,
        min_subscribers: int = 0,
        max_subscribers: int = 100_000_000,
        category: str | None = None,
        reference_channel_id: str | None = None,
        channel_ids: list[str] | None = None,
        comparison_ids: list[str] | None = None,
        format_type: str = "主要內容",
        content_topic: str = "全部",
        organization_scope: str = "peer",
        include_graduated: bool = False,
    ) -> dict[str, Any]:
        days = max(7, min(365, int(days)))
        min_subscribers = max(0, int(min_subscribers))
        max_subscribers = max(min_subscribers, min(100_000_000, int(max_subscribers)))
        selected_ids = list(dict.fromkeys(value for value in (channel_ids or []) if value))[:20]
        comparison_ids = list(dict.fromkeys(value for value in (comparison_ids or []) if value))[:5]
        allowed_formats = {"主要內容", "直播", "影片", "Shorts", "全部"}
        if format_type not in allowed_formats:
            format_type = "主要內容"
        allowed_topics = {*CONTENT_TOPIC_ORDER, "全部"}
        if content_topic not in allowed_topics:
            content_topic = "全部"
        if organization_scope not in {"peer", "all"}:
            organization_scope = "peer"

        conditions = ["discovery_status='eligible'"]
        params: list[Any] = []
        if selected_ids:
            conditions.append(f"channel_id IN ({','.join('?' for _ in selected_ids)})")
            params.extend(selected_ids)
        else:
            conditions.append("COALESCE(subscriber_count,0) BETWEEN ? AND ?")
            params.extend([min_subscribers, max_subscribers])
        if category and category != "全部":
            conditions.append("category=?")
            params.append(category)
        if not include_graduated:
            conditions.append("activity_status<>'已確認畢業'")
        if reference_channel_id:
            conditions.append("channel_id<>?")
            params.append(reference_channel_id)
        cohort = self.database.rows(
            f"""SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                       video_count,category,organization_name,activity_status,updated_at
                FROM channels WHERE {' AND '.join(conditions)}
                ORDER BY subscriber_count DESC""",
            tuple(params),
        )
        reference_rows = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                      video_count,category,organization_name,activity_status,updated_at
               FROM channels WHERE channel_id=? AND discovery_status IN ('eligible','owned')""",
            (reference_channel_id,),
        ) if reference_channel_id else []
        reference = reference_rows[0] if reference_rows else None

        organization_channels = list(cohort)
        if organization_scope == "all":
            organization_conditions = ["discovery_status='eligible'"]
            organization_params: list[Any] = []
            if category and category != "全部":
                organization_conditions.append("category=?")
                organization_params.append(category)
            if not include_graduated:
                organization_conditions.append("activity_status<>'已確認畢業'")
            organization_channels = self.database.rows(
                f"""SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                           video_count,category,organization_name,activity_status,updated_at
                    FROM channels WHERE {' AND '.join(organization_conditions)}
                    ORDER BY subscriber_count DESC""",
                tuple(organization_params),
            )

        data_channels = list(cohort)
        if reference and all(row["channel_id"] != reference["channel_id"] for row in data_channels):
            data_channels.append(reference)
        if comparison_ids:
            comparison_rows = self.database.rows(
                f"""SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                           video_count,category,organization_name,activity_status,updated_at
                    FROM channels WHERE channel_id IN ({','.join('?' for _ in comparison_ids)})
                      AND discovery_status IN ('eligible','owned')""",
                tuple(comparison_ids),
            )
            for row in comparison_rows:
                if all(existing["channel_id"] != row["channel_id"] for existing in data_channels):
                    data_channels.append(row)
        trend_data_ids = {row["channel_id"] for row in data_channels}
        for row in organization_channels:
            if all(existing["channel_id"] != row["channel_id"] for existing in data_channels):
                data_channels.append(row)
        data_ids = [row["channel_id"] for row in data_channels]
        channel_lookup = {row["channel_id"]: row for row in data_channels}
        now = datetime.now(UTC)
        sixty_day_cutoff = (now - timedelta(days=60)).isoformat(timespec="seconds")
        series_cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")

        videos: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        if data_ids:
            for group in chunks(data_ids, 400):
                placeholders = ",".join("?" for _ in group)
                videos.extend(self.database.rows(
                    f"""SELECT v.video_id,v.channel_id,v.title,v.description,v.thumbnail_url,
                               v.published_at,v.duration_seconds,v.content_type,v.content_tags,
                               v.view_count,v.live_state,v.actual_start,v.scheduled_start,
                               MAX(cs.concurrent_viewers) AS peak_concurrent
                        FROM videos v LEFT JOIN concurrency_samples cs ON cs.video_id=v.video_id
                        WHERE v.channel_id IN ({placeholders})
                          AND datetime(COALESCE(v.published_at,v.actual_start,v.scheduled_start,v.updated_at))
                              >= datetime(?)
                        GROUP BY v.video_id""",
                    tuple([*group, sixty_day_cutoff]),
                ))
                snapshots.extend(self.database.rows(
                    f"""SELECT channel_id,captured_at,subscriber_count,view_count,video_count
                        FROM channel_snapshots WHERE channel_id IN ({placeholders})
                        ORDER BY captured_at ASC""",
                    tuple(group),
                ))

        def accepted_format(video: dict[str, Any]) -> bool:
            actual = video_format(
                video["live_state"], video["duration_seconds"], video["title"], video["description"] or ""
            )
            video["format_type"] = actual
            video["attributes"] = [
                label for label in parse_json_list(video.get("content_tags"))
                if label in CONTENT_ATTRIBUTES
            ]
            if format_type == "全部":
                return True
            if format_type == "主要內容":
                return actual != "Shorts"
            return actual == format_type

        videos = [video for video in videos if accepted_format(video)]
        snapshots_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for snapshot in snapshots:
            snapshots_by_channel[snapshot["channel_id"]].append(snapshot)

        def snapshot_delta(channel: dict[str, Any], period: int, field: str) -> dict[str, Any]:
            current = channel.get(field)
            target = now - timedelta(days=period)
            candidates = [
                row for row in snapshots_by_channel[channel["channel_id"]]
                if (parsed := parse_api_time(row["captured_at"])) and parsed <= target and row.get(field) is not None
            ]
            previous_row = candidates[-1] if candidates else None
            previous = previous_row.get(field) if previous_row else None
            change = int(current) - int(previous) if current is not None and previous is not None else None
            percent_change = (
                change / int(previous) * 100 if change is not None and int(previous or 0) else None
            )
            return {
                "period_days": period,
                "current": current,
                "previous": previous,
                "change": change,
                "percent_change": percent_change,
                "basis_at": previous_row["captured_at"] if previous_row else None,
                "ready": previous_row is not None,
            }

        def rolling_delta(current: float | None, previous: float | None, period: int = 30) -> dict[str, Any]:
            change = current - previous if current is not None and previous is not None else None
            return {
                "period_days": period,
                "current": current,
                "previous": previous,
                "change": change,
                "percent_change": (
                    change / previous * 100 if change is not None and previous not in (None, 0) else None
                ),
                "basis_at": (now - timedelta(days=period)).isoformat(timespec="seconds") if previous is not None else None,
                "ready": previous is not None,
            }

        def published_time(video: dict[str, Any]) -> datetime | None:
            return parse_api_time(video.get("published_at") or video.get("actual_start") or video.get("scheduled_start"))

        metric_cache: dict[str, dict[str, Any]] = {}

        def channel_metrics(channel: dict[str, Any]) -> dict[str, Any]:
            cached = metric_cache.get(channel["channel_id"])
            if cached is not None:
                return cached
            channel_videos = [video for video in videos if video["channel_id"] == channel["channel_id"]]
            current_videos = [
                video for video in channel_videos
                if (published := published_time(video)) and published >= now - timedelta(days=30)
            ]
            previous_videos = [
                video for video in channel_videos
                if (published := published_time(video))
                and now - timedelta(days=60) <= published < now - timedelta(days=30)
            ]
            subscribers = int(channel.get("subscriber_count") or 0)

            def median_field(source: list[dict[str, Any]], field: str) -> float | None:
                return percentile((row.get(field) for row in source), .5)

            current_views = median_field(current_videos, "view_count")
            previous_views = median_field(previous_videos, "view_count")
            current_peak = median_field(current_videos, "peak_concurrent")
            previous_peak = median_field(previous_videos, "peak_concurrent")
            stickiness = current_views / subscribers * 100 if current_views is not None and subscribers else None
            previous_stickiness = (
                previous_views / subscribers * 100 if previous_views is not None and subscribers else None
            )
            ccv_rate = current_peak / subscribers * 100 if current_peak is not None and subscribers else None
            previous_ccv_rate = (
                previous_peak / subscribers * 100 if previous_peak is not None and subscribers else None
            )
            result = {
                **channel,
                "is_reference": bool(reference and channel["channel_id"] == reference["channel_id"]),
                "recent_items": len(current_videos),
                "previous_items": len(previous_videos),
                "median_views": current_views,
                "median_peak_concurrent": current_peak,
                "stickiness": stickiness,
                "ccv_rate": ccv_rate,
                "subscriber_delta_7": snapshot_delta(channel, 7, "subscriber_count"),
                "subscriber_delta_30": snapshot_delta(channel, 30, "subscriber_count"),
                "view_delta_30": snapshot_delta(channel, 30, "view_count"),
                "median_views_delta": rolling_delta(current_views, previous_views),
                "stickiness_delta": rolling_delta(stickiness, previous_stickiness),
                "ccv_rate_delta": rolling_delta(ccv_rate, previous_ccv_rate),
            }
            metric_cache[channel["channel_id"]] = result
            return result

        cohort_metrics = [channel_metrics(channel) for channel in cohort]
        reference_metrics = channel_metrics(reference) if reference else None
        ranking_pool = [*cohort_metrics, *([reference_metrics] if reference_metrics else [])]
        selected_series_ids = list(dict.fromkeys([
            *([reference["channel_id"]] if reference else []), *comparison_ids,
        ]))[:5]
        metrics_lookup = {row["channel_id"]: row for row in ranking_pool}
        for channel_id in selected_series_ids:
            if channel_id not in metrics_lookup and channel_id in channel_lookup:
                metrics_lookup[channel_id] = channel_metrics(channel_lookup[channel_id])
        comparison_channels = [
            metrics_lookup[channel_id]
            for channel_id in selected_series_ids
            if channel_id in metrics_lookup
        ]

        def ranked(metric: str, limit: int = 50, reverse: bool = True) -> list[dict[str, Any]]:
            return sorted(
                ranking_pool,
                key=lambda row: row.get(metric) if row and row.get(metric) is not None else -1,
                reverse=reverse,
            )[:limit]

        growth_7 = sorted(
            ranking_pool,
            key=lambda row: (
                row["subscriber_delta_7"]["ready"],
                row["subscriber_delta_7"]["percent_change"]
                if row["subscriber_delta_7"]["percent_change"] is not None else -1,
            ),
            reverse=True,
        )
        growth_30 = sorted(
            ranking_pool,
            key=lambda row: (
                row["subscriber_delta_30"]["ready"],
                row["subscriber_delta_30"]["percent_change"]
                if row["subscriber_delta_30"]["percent_change"] is not None else -1,
            ),
            reverse=True,
        )

        ranked_videos: list[dict[str, Any]] = []
        ranking_channel_ids = {row["channel_id"] for row in ranking_pool}
        for video in videos:
            if video["channel_id"] not in ranking_channel_ids:
                continue
            published = published_time(video)
            if not published or published < now - timedelta(days=30):
                continue
            video_topics = [part.strip() for part in str(video.get("content_type") or "其他").split("+")]
            if content_topic != "全部" and content_topic not in video_topics:
                continue
            channel = channel_lookup.get(video["channel_id"], {})
            subscribers = int(channel.get("subscriber_count") or 0)
            ranked_videos.append({
                "video_id": video["video_id"], "channel_id": video["channel_id"],
                "title": video["title"], "channel_title": channel.get("title", video["channel_id"]),
                "thumbnail_url": video["thumbnail_url"], "view_count": video["view_count"],
                "view_rate": (
                    float(video["view_count"]) / subscribers * 100
                    if subscribers and video["view_count"] is not None else None
                ),
                "format_type": video["format_type"], "content_type": video["content_type"],
                "attributes": video["attributes"], "published_at": video["published_at"],
            })
        ranked_videos.sort(key=lambda row: row["view_count"] if row["view_count"] is not None else -1, reverse=True)

        organizations: list[dict[str, Any]] = []
        organization_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        organization_metrics = [channel_metrics(channel) for channel in organization_channels]
        for row in organization_metrics:
            if row.get("organization_name"):
                organization_groups[row["organization_name"]].append(row)
        for name, members in organization_groups.items():
            organizations.append({
                "organization_name": name,
                "members": len(members),
                "subscriber_count": sum(int(row.get("subscriber_count") or 0) for row in members),
                "median_views_total": sum(float(row.get("median_views") or 0) for row in members),
                "median_stickiness": percentile((row.get("stickiness") for row in members), .5),
            })
        organizations.sort(key=lambda row: row["median_views_total"], reverse=True)

        series_rows: list[dict[str, Any]] = []
        peer_daily: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        if selected_series_ids:
            for channel_id in selected_series_ids:
                daily: dict[str, dict[str, Any]] = {}
                for row in snapshots_by_channel.get(channel_id, []):
                    if row["captured_at"] >= series_cutoff:
                        daily[row["captured_at"][:10]] = row
                series_rows.append({
                    "channel_id": channel_id,
                    "title": channel_lookup.get(channel_id, {}).get("title", channel_id),
                    "points": [
                        {
                            "date": date,
                            "subscriber_count": row["subscriber_count"],
                            "view_count": row["view_count"],
                            "video_count": row["video_count"],
                        }
                        for date, row in sorted(daily.items())
                    ],
                })
        for channel in cohort:
            daily: dict[str, dict[str, Any]] = {}
            for row in snapshots_by_channel.get(channel["channel_id"], []):
                if row["captured_at"] >= series_cutoff:
                    daily[row["captured_at"][:10]] = row
            for date, row in daily.items():
                for field in ("subscriber_count", "view_count", "video_count"):
                    if row[field] is not None:
                        peer_daily[date][field].append(int(row[field]))
        peer_series = [
            {
                "date": date,
                "subscriber_count": percentile(values["subscriber_count"], .5),
                "view_count": percentile(values["view_count"], .5),
                "video_count": percentile(values["video_count"], .5),
            }
            for date, values in sorted(peer_daily.items())
        ]

        oldest_snapshot = min(
            (
                parse_api_time(row["captured_at"])
                for row in snapshots
                if row["channel_id"] in trend_data_ids and parse_api_time(row["captured_at"])
            ),
            default=None,
        )
        collected_days = max(0.0, (now - oldest_snapshot).total_seconds() / 86400) if oldest_snapshot else 0.0
        private_metrics = None
        if reference and reference["channel_id"] == self.owned_channel_id():
            private_metrics = self.creator_dashboard().get("imported_overview")

        return {
            "generated_at": utc_now(),
            "filters": {
                "days": days, "min_subscribers": min_subscribers,
                "max_subscribers": max_subscribers, "category": category or "全部",
                "channel_ids": selected_ids, "comparison_ids": comparison_ids,
                "format_type": format_type, "content_topic": content_topic,
                "organization_scope": organization_scope,
                "include_graduated": include_graduated,
            },
            "reference": reference_metrics,
            "comparison_channels": comparison_channels,
            "overview": {
                "peer_channels": len(cohort_metrics),
                "active_channels": sum(1 for row in cohort_metrics if row["recent_items"] > 0),
                "median_subscribers": percentile((row.get("subscriber_count") for row in cohort_metrics), .5),
                "median_views": percentile((row.get("median_views") for row in cohort_metrics), .5),
                "median_stickiness": percentile((row.get("stickiness") for row in cohort_metrics), .5),
            },
            "rankings": {
                "subscribers": ranked("subscriber_count"),
                "growth_7": growth_7[:50],
                "growth_30": growth_30[:50],
                "median_views": ranked("median_views"),
                "stickiness": ranked("stickiness"),
                "ccv_rate": ranked("ccv_rate"),
                "top_videos": ranked_videos[:30],
                "organizations": organizations,
            },
            "organization_coverage": {
                "scope_channels": len(organization_metrics),
                "named_channels": sum(1 for row in organization_metrics if row.get("organization_name")),
            },
            "series": series_rows,
            "peer_series": peer_series,
            "readiness": {
                "oldest_snapshot_at": oldest_snapshot.isoformat(timespec="seconds") if oldest_snapshot else None,
                "collected_days": collected_days,
                "week_ready": collected_days >= 7,
                "month_ready": collected_days >= 30,
            },
            "private_metrics": private_metrics,
        }

    def refresh_manual_queue(self, limit: int = 50) -> dict[str, int]:
        queued = self.database.manual_refresh_queue(limit)
        ids = [row["channel_id"] for row in queued]
        if not ids:
            return {"requested": 0, "updated": 0, "failed": 0, "remaining": 0}
        try:
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(ids),
                "maxResults": len(ids),
            })
            returned: list[str] = []
            for item in payload.get("items", []):
                self.database.upsert_channel(item)
                returned.append(item["id"])
            missing = [channel_id for channel_id in ids if channel_id not in returned]
            self.database.complete_manual_refresh(returned)
            self.database.fail_manual_refresh(missing, "YouTube 未回傳此頻道")
            self.evaluate_activity_statuses(returned)
            return {
                "requested": len(ids),
                "updated": len(returned),
                "failed": len(missing),
                "remaining": int(self.database.scalar("SELECT COUNT(*) FROM manual_refresh_queue") or 0),
            }
        except Exception as error:
            self.database.fail_manual_refresh(ids, str(error))
            raise

    def refresh_channels(self) -> None:
        ids = [row["channel_id"] for row in self.database.rows(
            "SELECT channel_id FROM channels WHERE discovery_status IN ('eligible','review','owned')"
        )]
        for group in chunks(ids):
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                self.database.upsert_channel(item)
                current = self.database.rows(
                    "SELECT discovery_status,subscriber_count FROM channels WHERE channel_id=?",
                    (item["id"],),
                )
                if current and current[0]["discovery_status"] == "owned" and int(current[0]["subscriber_count"] or 0) >= self.settings_payload()["min_subscribers"]:
                    self.database.execute(
                        "UPDATE channels SET discovery_status='eligible' WHERE channel_id=?",
                        (item["id"],),
                    )
        self.evaluate_activity_statuses(ids)

    def refresh_videos(self, video_ids: list[str]) -> None:
        for group in chunks(list(dict.fromkeys(video_ids))):
            payload = self.youtube.get("videos", {
                "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                self.database.upsert_video(item)

    def scan_hourly_live_candidates(self) -> dict[str, int | bool]:
        owned_channel_id = self.owned_channel_id()
        channels = self.database.rows(
            """SELECT channel_id,title,uploads_playlist_id FROM channels
               WHERE discovery_status IN ('eligible','owned') AND uploads_playlist_id IS NOT NULL
               ORDER BY CASE WHEN channel_id=? THEN 0 ELSE 1 END,channel_id""",
            (owned_channel_id,),
        )
        if not channels:
            return {"channels": 0, "candidates": 0, "refreshed": 0, "quota_limited": False}

        usage = self.youtube.usage("general")
        safe_limit = self.youtube.safe_limit("general")
        estimated_detail_calls = max(1, (len(channels) * 5 + 49) // 50)
        scan_all = (
            usage + len(channels) + estimated_detail_calls
            <= max(0, safe_limit - HOURLY_LIVE_SCAN_QUOTA_RESERVE)
        )
        selected = channels if scan_all else [
            channel for channel in channels if channel["channel_id"] == owned_channel_id
        ]
        if not selected or safe_limit - usage < 2:
            return {"channels": 0, "candidates": 0, "refreshed": 0, "quota_limited": True}

        candidate_ids: list[str] = []
        for channel in selected:
            try:
                payload = self.youtube.get("playlistItems", {
                    "part": "contentDetails,snippet",
                    "playlistId": channel["uploads_playlist_id"],
                    "maxResults": 5,
                })
            except Exception as error:
                if not self._is_missing_upload_playlist(error):
                    raise
                self._record_upload_playlist_warning(channel, error)
                continue
            candidate_ids.extend(
                item.get("contentDetails", {}).get("videoId")
                for item in payload.get("items", [])
                if item.get("contentDetails", {}).get("videoId")
            )

        unique_ids = list(dict.fromkeys(candidate_ids))
        existing_states: dict[str, str] = {}
        for group in chunks(unique_ids):
            placeholders = ",".join("?" for _ in group)
            for row in self.database.rows(
                f"SELECT video_id,live_state FROM videos WHERE video_id IN ({placeholders})",
                tuple(group),
            ):
                existing_states[row["video_id"]] = row["live_state"]
        refresh_ids = [
            video_id for video_id in unique_ids
            if video_id not in existing_states or existing_states[video_id] in {"live", "upcoming"}
        ]
        if refresh_ids:
            self.refresh_videos(refresh_ids)
        return {
            "channels": len(selected),
            "candidates": len(unique_ids),
            "refreshed": len(refresh_ids),
            "quota_limited": not scan_all,
        }

    def scan_due_uploads(self, limit: int = 10) -> None:
        upload_scan_hours = self.settings_payload()["upload_scan_hours"]
        cutoff = (datetime.now(UTC) - timedelta(hours=upload_scan_hours)).isoformat(timespec="seconds")
        due = self.database.rows(
            """SELECT channel_id,title,uploads_playlist_id FROM channels
               WHERE discovery_status IN ('eligible','owned') AND uploads_playlist_id IS NOT NULL
                 AND (last_upload_scan_at IS NULL OR last_upload_scan_at < ?)
               ORDER BY COALESCE(last_upload_scan_at,'') ASC LIMIT ?""",
            (cutoff, limit),
        )
        video_ids: list[str] = []
        for channel in due:
            try:
                payload = self.youtube.get("playlistItems", {
                    "part": "contentDetails,snippet", "playlistId": channel["uploads_playlist_id"],
                    "maxResults": 10,
                })
            except Exception as error:
                if not self._is_missing_upload_playlist(error):
                    raise
                self._record_upload_playlist_warning(channel, error)
                self.database.execute(
                    "UPDATE channels SET last_upload_scan_at=? WHERE channel_id=?",
                    (utc_now(), channel["channel_id"]),
                )
                continue
            video_ids.extend(
                item.get("contentDetails", {}).get("videoId")
                for item in payload.get("items", [])
                if item.get("contentDetails", {}).get("videoId")
            )
            self.database.execute(
                "UPDATE channels SET last_upload_scan_at=? WHERE channel_id=?",
                (utc_now(), channel["channel_id"]),
            )
        if video_ids:
            self.refresh_videos(video_ids)
        self.evaluate_activity_statuses([channel["channel_id"] for channel in due])

    def poll_live(self) -> None:
        threshold = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
        ids = [row["video_id"] for row in self.database.rows(
            """SELECT video_id FROM videos
               WHERE live_state='live' OR (live_state='upcoming' AND scheduled_start <= ?)""",
            (threshold,),
        )]
        if ids:
            self.refresh_videos(ids)

    def cleanup(self) -> None:
        settings = self.settings_payload()
        retention_days = settings["retention_days"]
        if retention_days > 0:
            cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="seconds")
            self.database.execute("DELETE FROM concurrency_samples WHERE captured_at < ?", (cutoff,))
            self.database.execute("DELETE FROM channel_snapshots WHERE captured_at < ?", (cutoff,))
            self.database.execute("DELETE FROM video_snapshots WHERE captured_at < ?", (cutoff,))
        creator_retention_days = settings["creator_retention_days"]
        if creator_retention_days > 0:
            creator_cutoff = (datetime.now(UTC) - timedelta(days=creator_retention_days)).date().isoformat()
            self.database.execute(
                "DELETE FROM creator_analytics_rows WHERE COALESCE(event_date, substr(created_at,1,10)) < ?",
                (creator_cutoff,),
            )
            self.database.execute(
                "DELETE FROM creator_manual_metrics WHERE metric_date < ?",
                (creator_cutoff,),
            )
            self.database.execute(
                "DELETE FROM creator_oauth_daily_metrics WHERE event_date < ?",
                (creator_cutoff,),
            )
            self.database.execute(
                """DELETE FROM creator_import_batches
                   WHERE NOT EXISTS (
                     SELECT 1 FROM creator_analytics_rows r WHERE r.batch_id=creator_import_batches.id
                   )"""
            )

    def _scheduled(self, name: str, callback: Callable[[], None]) -> None:
        if not self.run_lock.acquire(blocking=False):
            return
        self._set_job(name)
        try:
            callback()
            with self.state_lock:
                self.last_error = None
        except Exception as error:
            self._record_error(error)
        finally:
            self._set_job(None)
            self.run_lock.release()

    def _scheduler(self) -> None:
        while self.running:
            now = time.monotonic()
            settings = self.settings_payload()
            if self.config.api_key:
                if now - self.last_live_poll >= settings["live_poll_seconds"]:
                    self._scheduled("live-poll", self.poll_live)
                    self.last_live_poll = now
                live_scan_slot = hourly_live_scan_slot()
                if live_scan_slot and live_scan_slot != self.last_hourly_live_scan_slot:
                    self.last_hourly_live_scan_slot = live_scan_slot
                    self._scheduled("hourly-live-scan", self.scan_hourly_live_candidates)
                if now - self.last_upload_dispatch >= 60:
                    self._scheduled("upload-scan", lambda: self.scan_due_uploads(limit=10))
                    self.last_upload_dispatch = now
                if now - self.last_channel_refresh >= settings["channel_refresh_hours"] * 3600:
                    self._scheduled("channel-refresh", self.refresh_channels)
                    self.last_channel_refresh = now
                queued = int(self.database.scalar("SELECT COUNT(*) FROM manual_refresh_queue") or 0)
                if queued >= 50 and now - self.last_manual_queue_dispatch >= 60:
                    self._scheduled("manual-channel-refresh", self.refresh_manual_queue)
                    self.last_manual_queue_dispatch = now
            if now - self.last_oauth_sync_check >= 300:
                self.last_oauth_sync_check = now
                try:
                    needs_oauth_sync = bool(self.database.scalar(
                        """SELECT 1 FROM creator_oauth_connections
                           WHERE last_sync_at IS NULL
                              OR datetime(last_sync_at)<=datetime('now','-24 hours') LIMIT 1"""
                    ))
                    if needs_oauth_sync and self.oauth.status()["authorized"]:
                        self._scheduled("oauth-analytics-sync", self.sync_creator_oauth)
                except Exception as error:
                    self._record_error(error)
            if now - self.last_cleanup >= 86400:
                self.cleanup()
                self.last_cleanup = now
            time.sleep(3)

    def summary(self) -> dict[str, Any]:
        settings = self.settings_payload()
        channels = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,video_count,
                      category,organization_name,manual_tags,activity_status,activity_status_source,
                      activity_status_confidence,activity_status_reason,activity_status_detected_at,
                      activity_status_reviewed_at,activity_status_manual_lock,last_activity_at,
                      match_term,match_field,match_excerpt,updated_at
               FROM channels WHERE discovery_status='eligible'
               ORDER BY subscriber_count DESC LIMIT 500"""
        )
        for channel in channels:
            channel["manual_tags"] = parse_json_list(channel.get("manual_tags"))
        live_videos = self.database.rows(
            """SELECT v.video_id,v.channel_id,v.title,c.title AS channel_title,v.thumbnail_url,v.live_state,
                      v.current_concurrent,v.scheduled_start,v.actual_start,v.updated_at
               FROM videos v JOIN channels c ON c.channel_id=v.channel_id
               WHERE v.live_state IN ('live','upcoming')
               ORDER BY CASE v.live_state WHEN 'live' THEN 0 ELSE 1 END,
                        COALESCE(v.current_concurrent,0) DESC, v.scheduled_start ASC LIMIT 100"""
        )
        with self.state_lock:
            job = self.current_job
            job_started_at = self.current_job_started_at
            last_job = self.last_job
            last_job_finished_at = self.last_job_finished_at
            last_job_status = self.last_job_status
            error = self.last_error
            warning = self.last_warning
            progress = dict(self.discovery_progress)
        search_usage = self.youtube.usage("search")
        general_usage = self.youtube.usage("general")
        owned_channel_id = self.owned_channel_id()
        owned_rows = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,video_count,
                      category,organization_name,manual_tags,activity_status
               FROM channels WHERE channel_id=?""",
            (owned_channel_id,),
        ) if owned_channel_id else []
        if owned_rows:
            owned_rows[0]["manual_tags"] = parse_json_list(owned_rows[0].get("manual_tags"))
        manual_queue_count = int(self.database.scalar(
            "SELECT COUNT(*) FROM manual_refresh_queue"
        ) or 0)
        manual_queue_failed = int(self.database.scalar(
            "SELECT COUNT(*) FROM manual_refresh_queue WHERE attempts>0"
        ) or 0)
        return {
            "api_key_configured": bool(self.config.api_key),
            "collector_running": self.running,
            "current_job": job,
            "current_job_started_at": job_started_at,
            "last_job": last_job,
            "last_job_finished_at": last_job_finished_at,
            "last_job_status": last_job_status,
            "last_error": error,
            "last_warning": warning,
            "eligible_channels": int(self.database.scalar(
                "SELECT COUNT(*) FROM channels WHERE discovery_status='eligible'"
            ) or 0),
            "review_channels": int(self.database.scalar(
                "SELECT COUNT(*) FROM channels WHERE discovery_status='review'"
            ) or 0),
            "excluded_channels": int(self.database.scalar(
                "SELECT COUNT(*) FROM excluded_channels"
            ) or 0),
            "live_count": int(self.database.scalar(
                "SELECT COUNT(*) FROM videos WHERE live_state='live'"
            ) or 0),
            "upcoming_count": int(self.database.scalar(
                "SELECT COUNT(*) FROM videos WHERE live_state='upcoming'"
            ) or 0),
            "sample_count": int(self.database.scalar("SELECT COUNT(*) FROM concurrency_samples") or 0),
            "quota_general": general_usage,
            "quota_search": search_usage,
            "quota_general_limit": self.config.quota_general_limit,
            "quota_search_limit": self.config.quota_search_limit,
            "quota_general_safe_limit": self.youtube.safe_limit("general"),
            "quota_search_safe_limit": self.youtube.safe_limit("search"),
            "search_quota_available": search_usage < self.youtube.safe_limit("search"),
            "quota_reset_at": self.youtube.reset_at(),
            "owned_channel_id": owned_channel_id,
            "owned_channel": owned_rows[0] if owned_rows else None,
            "manual_refresh_queue_count": manual_queue_count,
            "manual_refresh_queue_threshold": 50,
            "manual_refresh_queue_failed": manual_queue_failed,
            "discovery_progress": progress,
            "retention_days": settings["retention_days"],
            "settings": settings,
            "categories": self.database.rows(
                """SELECT category,COUNT(*) AS channel_count FROM channels
                   WHERE discovery_status='eligible' GROUP BY category ORDER BY category"""
            ),
            "organizations": self.database.rows(
                """SELECT organization_name,COUNT(*) AS channel_count FROM channels
                   WHERE discovery_status='eligible' AND organization_name<>''
                   GROUP BY organization_name ORDER BY organization_name"""
            ),
            "activity_statuses": self.database.rows(
                """SELECT activity_status,COUNT(*) AS channel_count FROM channels
                   WHERE discovery_status='eligible' GROUP BY activity_status ORDER BY activity_status"""
            ),
            "activity_review_count": int(self.database.scalar(
                """SELECT COUNT(*) FROM channels WHERE discovery_status='eligible'
                   AND activity_status IN ('休止中','疑似已畢業')
                   AND activity_status_manual_lock=0"""
            ) or 0),
            "channels": channels,
            "live_videos": live_videos,
        }


class RequestHandler(BaseHTTPRequestHandler):
    service: TrackerService
    trusted_origins = {"http://127.0.0.1:3000", "http://localhost:3000"}

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _headers(
        self,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        origin = self.headers.get("Origin")
        if origin in self.trusted_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            if length > 12 * 1024 * 1024:
                raise ValueError("Request body too large")
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}

    def _body_bytes(self, limit: int = MAX_PACKAGE_BYTES) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("無法判斷上傳檔案大小") from error
        if length <= 0:
            raise ValueError("請先選擇公開監測資料 ZIP")
        if length > limit:
            raise ValueError("資料包超過 256 MB 上傳上限")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("資料包上傳不完整，請重新選擇檔案")
        return body

    def _binary(self, payload: bytes, filename: str) -> None:
        safe_filename = re.sub(r"[^A-Za-z0-9._-]", "-", filename) or "tai-v-pulse-public-data.zip"
        self._headers(
            200,
            "application/zip",
            {
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Content-Length": str(len(payload)),
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
        self.wfile.write(payload)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in self.trusted_origins

    def _reject_untrusted_origin(self) -> bool:
        if self._origin_allowed():
            return False
        self._json({"error": "不允許其他網站存取台V Pulse 本機 API"}, 403)
        return True

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _html(self, content: str, status: int = 200) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _oauth_help_html(raw_error: str) -> str:
        guidance = oauth_error_guidance(raw_error)
        steps = "".join(f"<li>{html.escape(step)}</li>" for step in guidance["steps"])
        link = (
            f"<p><a href='{html.escape(guidance['help_url'])}' target='_blank' rel='noreferrer'>"
            f"{html.escape(guidance['help_label'])} ↗</a></p>"
            if guidance.get("help_url") and guidance.get("help_label") else ""
        )
        return (
            f"<h2>{html.escape(guidance['title'])}</h2>"
            f"<p>{html.escape(guidance['message'])}</p><ol>{steps}</ol>{link}"
            f"<details><summary>技術細節</summary><pre style='white-space:pre-wrap'>"
            f"{html.escape(raw_error)}</pre></details>"
        )

    def do_OPTIONS(self) -> None:
        if self._reject_untrusted_origin():
            return
        self._headers(204)

    def do_GET(self) -> None:
        if self._reject_untrusted_origin():
            return
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route == "/api/public-data/export":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                filename, payload, _ = self.service.database.export_public_monitoring(
                    include_blacklist=query.get("include_blacklist", ["false"])[0].lower() == "true",
                    include_source_evidence=query.get(
                        "include_source_evidence", ["false"]
                    )[0].lower() == "true",
                )
                self._binary(payload, filename)
            except (OSError, sqlite3.Error, ValueError) as error:
                self._json({"error": f"無法建立公開監測資料包：{error}"}, 500)
        elif route == "/api/creator/oauth/start":
            try:
                self._redirect(self.service.creator_oauth_authorization_url())
            except (ValueError, OAuthError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator/oauth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if query.get("error"):
                    error_code = query.get("error", [""])[0]
                    description = query.get("error_description", [""])[0]
                    raise ValueError(f"{error_code}: {description}".strip(": "))
                result = self.service.complete_creator_oauth(
                    query.get("code", [""])[0], query.get("state", [""])[0]
                )
                warning = str(result.get("warning") or "")
                detail = (
                    "<p>已完成第一次 Analytics 同步。</p>" if not warning else
                    "<p>頻道已連結，但第一次同步尚未完成。</p>" + self._oauth_help_html(warning)
                )
                self._html(
                    "<!doctype html><meta charset='utf-8'><title>台V Pulse 已連結</title>"
                    "<body style='font-family:system-ui;padding:32px;max-width:680px;margin:auto'>"
                    "<h1>頻道已安全連結</h1>"
                    f"{detail}<p>你可以關閉此分頁並回到台V Pulse。</p>"
                    "<script>if(window.opener){window.opener.postMessage('tai-v-pulse-oauth-complete','http://127.0.0.1:3000')}</script>"
                    "</body>"
                )
            except (ValueError, OAuthError) as error:
                error_text = str(error)
                self._html(
                    "<!doctype html><meta charset='utf-8'><title>台V Pulse 連結失敗</title>"
                    "<body style='font-family:system-ui;padding:32px;max-width:680px;margin:auto'>"
                    f"<h1>連結未完成</h1>{self._oauth_help_html(error_text)}"
                    "<p>請關閉此分頁，回到台V Pulse 後重新嘗試。</p></body>",
                    400,
                )
        elif route == "/api/summary":
            self._json(self.service.summary())
        elif route == "/api/candidates":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                raw_batch_id = query.get("batch_id", [""])[0]
                batch_id = int(raw_batch_id) if raw_batch_id else None
                self._json(self.service.candidate_review(
                    query=query.get("query", [""])[0],
                    batch_id=batch_id,
                    validation_status=query.get("validation_status", ["all"])[0],
                    handling_status=query.get("handling_status", ["all"])[0],
                    sort=query.get("sort", ["discovered_at"])[0],
                    direction=query.get("direction", ["desc"])[0],
                ))
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator":
            query = urllib.parse.parse_qs(parsed.query)
            self._json(self.service.creator_dashboard(query.get("channel_id", [None])[0]))
        elif route == "/api/creator/analytics":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                self._json(self.service.creator_analytics(
                    query.get("channel_id", [None])[0],
                    query=query.get("query", [""])[0],
                    date_start=query.get("date_start", [""])[0],
                    date_end=query.get("date_end", [""])[0],
                    report_name=query.get("report_name", ["all"])[0],
                    row_kind=query.get("row_kind", ["detail"])[0],
                    status=query.get("status", ["all"])[0],
                    content_format=query.get("content_format", ["all"])[0],
                    content_topic=query.get("content_topic", ["all"])[0],
                    sort=query.get("sort", ["date"])[0],
                    direction=query.get("direction", ["desc"])[0],
                    page=int(query.get("page", ["1"])[0]),
                    page_size=int(query.get("page_size", ["50"])[0]),
                ))
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/insights":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                payload = self.service.insights(
                    days=int(query.get("days", ["30"])[0]),
                    min_subscribers=int(query.get("min_subscribers", ["0"])[0]),
                    max_subscribers=int(query.get("max_subscribers", ["10000000"])[0]),
                    category=query.get("category", [None])[0],
                    reference_channel_id=query.get("reference_channel_id", [None])[0],
                    include_graduated=query.get("include_graduated", ["false"])[0].lower() == "true",
                    channel_ids=[
                        value for value in query.get("channel_ids", [""])[0].split(",") if value
                    ],
                )
                self._json(payload)
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/trends":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                self._json(self.service.trends(
                    days=int(query.get("days", ["7"])[0]),
                    min_subscribers=int(query.get("min_subscribers", ["0"])[0]),
                    max_subscribers=int(query.get("max_subscribers", ["100000000"])[0]),
                    category=query.get("category", [None])[0],
                    reference_channel_id=query.get("reference_channel_id", [None])[0],
                    channel_ids=[value for value in query.get("channel_ids", [""])[0].split(",") if value],
                    comparison_ids=[value for value in query.get("comparison_ids", [""])[0].split(",") if value],
                    format_type=query.get("format_type", ["主要內容"])[0],
                    content_topic=query.get("content_topic", ["全部"])[0],
                    organization_scope=query.get("organization_scope", ["peer"])[0],
                    include_graduated=query.get("include_graduated", ["false"])[0].lower() == "true",
                ))
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
        elif route.startswith("/api/channels/"):
            channel_id = urllib.parse.unquote(route[len("/api/channels/"):]).strip()
            try:
                self._json(self.service.channel_detail(channel_id))
            except ValueError as error:
                self._json({"error": str(error)}, 404)
        elif route in ("/api/health", "/health"):
            self._json({"ok": True, "time": utc_now()})
        else:
            self._json({"error": "Not found"}, 404)

    def do_PATCH(self) -> None:
        if self._reject_untrusted_origin():
            return
        route = urllib.parse.urlparse(self.path).path
        body = self._body_json()
        try:
            if route == "/api/settings":
                settings = self.service.update_settings(body)
                self._json({"message": "收錄與監控規則已儲存", "settings": settings})
                return
            if route == "/api/creator":
                channel = self.service.set_owned_channel(str(body.get("channel_id", "")).strip())
                self._json({"message": f"已將 {channel['title']} 加入工作區並切換查看", "channel": channel})
                return
            candidate_prefix = "/api/candidates/"
            if route.startswith(candidate_prefix):
                candidate_id = int(route[len(candidate_prefix):])
                result = self.service.review_candidate(
                    candidate_id,
                    str(body.get("action", "")).strip(),
                )
                self._json(result)
                return
            prefix = "/api/channels/"
            if route.startswith(prefix):
                channel_id = urllib.parse.unquote(route[len(prefix):]).strip()
                channel = self.service.update_channel_metadata(channel_id, body)
                self._json({"message": "頻道資料已儲存", "channel": channel})
                return
            self._json({"error": "Not found"}, 404)
        except ValueError as error:
            self._json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        if self._reject_untrusted_origin():
            return
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/public-data/import-preview":
            try:
                preview = self.service.database.preview_public_monitoring(self._body_bytes())
                self._json({"preview": preview})
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/public-data/import":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            mode = query.get("mode", [""])[0]
            if not self.service.run_lock.acquire(blocking=False):
                self._json({"error": "目前有背景更新正在執行，請稍後再匯入"}, 409)
                return
            self.service._set_job("public-data-import")
            try:
                result = self.service.database.import_public_monitoring(self._body_bytes(), mode)
                action = "合併" if mode == "merge" else "取代"
                self._json({
                    "message": f"公開監測資料已{action}完成；API Key 與私人資料未變更",
                    "result": result,
                }, 201)
            except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as error:
                self.service._record_error(error)
                self._json({"error": str(error)}, 400)
            finally:
                self.service._set_job(None)
                self.service.run_lock.release()
        elif route == "/api/creator/oauth/client":
            try:
                self._json({
                    "message": "OAuth 桌面應用程式設定已加密保存",
                    "oauth": self.service.configure_creator_oauth(self._body_json()),
                }, 201)
            except (ValueError, OAuthError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator/oauth/sync":
            try:
                if not self.service.creator_oauth_status()["connected"]:
                    raise ValueError("尚未連結 YouTube 頻道")
                ok, message = self.service.launch_job(
                    "oauth-analytics-sync", self.service.sync_creator_oauth
                )
                self._json({"message": message} if ok else {"error": message}, 202 if ok else 409)
            except (ValueError, OAuthError) as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/discover":
            ok, message = self.service.launch_job("discover", self.service.discover_channels)
            self._json({"message": message} if ok else {"error": message}, 202 if ok else 409)
        elif route == "/api/scan":
            ok, message = self.service.launch_job("upload-scan", lambda: self.service.scan_due_uploads(50))
            self._json({"message": message} if ok else {"error": message}, 202 if ok else 409)
        elif route == "/api/manual-refresh":
            queued = int(self.service.database.scalar(
                "SELECT COUNT(*) FROM manual_refresh_queue"
            ) or 0)
            if not queued:
                self._json({"message": "目前沒有待更新的手動頻道", "queue_count": 0})
            else:
                ok, message = self.service.launch_job(
                    "manual-channel-refresh", self.service.refresh_manual_queue
                )
                self._json(
                    {"message": message, "queue_count": queued} if ok else {"error": message},
                    202 if ok else 409,
                )
        elif route == "/api/channel-search":
            body = self._body_json()
            try:
                if not self.service.config.api_key:
                    raise ValueError("尚未設定 YOUTUBE_API_KEY")
                with self.service.run_lock:
                    candidates = self.service.search_channels(str(body.get("query", "")))
                self._json({"candidates": candidates})
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            except Exception as error:
                self.service._record_error(error)
                self._json({"error": str(error)}, 502)
        elif route == "/api/creator/import-preview":
            body = self._body_json()
            try:
                self._json({"preview": self.service.preview_creator_import(body)})
            except ValueError as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator/import":
            body = self._body_json()
            try:
                batch = self.service.import_creator_data(body)
                message = "這個檔案先前已匯入，未重複寫入" if batch.get("already_imported") else "YouTube Studio 資料已匯入"
                self._json({"message": message, "batch": batch}, 200 if batch.get("already_imported") else 201)
            except ValueError as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator/manual":
            body = self._body_json()
            try:
                metric = self.service.add_creator_manual_metric(body)
                self._json({"message": "手動補充資料已儲存", "metric": metric}, 201)
            except ValueError as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/content-classification":
            try:
                video = self.service.update_video_classification(self._body_json())
                self._json({"message": "內容分類已確認，後續更新會保留這次修正", "video": video})
            except ValueError as error:
                self._json({"error": str(error)}, 400)
        elif route == "/api/creator/channel":
            body = self._body_json()
            try:
                channel_id = str(body.get("channel_id", "")).strip()
                if not channel_id:
                    raise ValueError("缺少 channel_id")
                with self.service.run_lock:
                    channel = self.service.add_owned_channel(channel_id)
                self._json({
                    "message": f"已收錄 {channel['title']} 並加入工作區",
                    "channel": channel,
                }, 201)
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            except Exception as error:
                self.service._record_error(error)
                self._json({"error": str(error)}, 502)
        elif route == "/api/channels":
            body = self._body_json()
            try:
                channel_id = str(body.get("channel_id", "")).strip()
                if not channel_id:
                    raise ValueError("缺少 channel_id")
                with self.service.run_lock:
                    channel = self.service.add_manual_channel(channel_id)
                self._json({"message": f"已收錄 {channel['title']}", "channel": channel}, 201)
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            except Exception as error:
                self.service._record_error(error)
                self._json({"error": str(error)}, 502)
        else:
            self._json({"error": "Not found"}, 404)

    def do_DELETE(self) -> None:
        if self._reject_untrusted_origin():
            return
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/creator/oauth":
            try:
                result = self.service.disconnect_creator_oauth()
                self._json({
                    "message": "已撤銷連線並刪除本機 OAuth 授權與同步資料",
                    **result,
                })
            except (ValueError, OAuthError) as error:
                self._json({"error": str(error)}, 400)
            return
        if route == "/api/creator/oauth/client":
            try:
                self.service.delete_creator_oauth_client()
                self._json({"message": "已刪除本機 OAuth 用戶端設定"})
            except (ValueError, OAuthError) as error:
                self._json({"error": str(error)}, 400)
            return
        workspace_prefix = "/api/creator/channels/"
        if route.startswith(workspace_prefix):
            try:
                channel_id = urllib.parse.unquote(route[len(workspace_prefix):]).strip()
                channel = self.service.remove_workspace_channel(channel_id)
                self._json({
                    "message": f"已將 {channel['title']} 移出工作區；公開監測與私人資料均保留",
                    "channel": channel,
                })
            except ValueError as error:
                self._json({"error": str(error)}, 404)
            return
        import_prefix = "/api/creator/imports/"
        if route.startswith(import_prefix):
            try:
                batch_id = int(route[len(import_prefix):])
                batch = self.service.delete_creator_import(batch_id)
                self._json({"message": f"已回復匯入批次：{batch['filename']}"})
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 404)
            return
        manual_prefix = "/api/creator/manual/"
        if route.startswith(manual_prefix):
            try:
                metric_id = int(route[len(manual_prefix):])
                self.service.delete_creator_manual_metric(metric_id)
                self._json({"message": "手動補充資料已刪除"})
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 404)
            return
        classification_prefix = "/api/content-classification/"
        if route.startswith(classification_prefix):
            try:
                video_id = urllib.parse.unquote(route[len(classification_prefix):]).strip()
                video = self.service.reset_video_classification(video_id)
                self._json({"message": "已改回自動分類", "video": video})
            except ValueError as error:
                self._json({"error": str(error)}, 404)
            return
        prefix = "/api/channels/"
        if not route.startswith(prefix):
            self._json({"error": "Not found"}, 404)
            return
        channel_id = urllib.parse.unquote(route[len(prefix):]).strip()
        try:
            channel = self.service.exclude_channel(channel_id)
            self._json({
                "message": f"已排除 {channel['title']}，之後探索不會自動加回",
                "channel": channel,
            })
        except ValueError as error:
            self._json({"error": str(error)}, 404)


def main() -> None:
    config = Config()
    service = TrackerService(config)
    service.start()
    RequestHandler.service = service
    server = ThreadingHTTPServer((config.host, config.port), RequestHandler)
    print(f"Tai V Pulse API: http://{config.host}:{config.port}", flush=True)
    print(f"Database: {config.database_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.running = False
        server.server_close()
        service.database.close()


if __name__ == "__main__":
    main()
