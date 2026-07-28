from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import locale
import os
import re
import sqlite3
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
    "views", "watch_time_hours", "average_view_duration_seconds",
    "average_percentage_viewed", "impressions", "impressions_ctr",
    "subscribers_net", "subscribers_gained", "subscribers_lost", "likes",
    "comments", "shares", "unique_viewers", "returning_viewers",
    "estimated_revenue",
)
CREATOR_SUM_METRICS = {
    "views", "watch_time_hours", "impressions", "subscribers_net",
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
    "views": ("views", "view count", "觀看次數", "瀏覽次數"),
    "watch_time_hours": ("watch time (hours)", "watch time hours", "觀看時間 (小時)", "觀看時間小時"),
    "average_view_duration_seconds": ("average view duration", "avg view duration", "平均觀看時間", "平均觀看時長"),
    "average_percentage_viewed": ("average percentage viewed", "avg percentage viewed", "平均觀看百分比"),
    "impressions": ("impressions", "曝光次數"),
    "impressions_ctr": ("impressions click-through rate", "impressions ctr", "曝光點閱率", "曝光點擊率"),
    "subscribers_net": ("subscribers", "net subscribers", "訂閱人數", "訂閱者"),
    "subscribers_gained": ("subscribers gained", "gained subscribers", "獲得的訂閱者", "新增訂閱人數"),
    "subscribers_lost": ("subscribers lost", "lost subscribers", "流失的訂閱者", "取消訂閱人數"),
    "likes": ("likes", "喜歡次數", "按讚數"),
    "comments": ("comments", "留言", "留言數"),
    "shares": ("shares", "分享", "分享次數"),
    "unique_viewers": ("unique viewers", "不重複觀眾人數", "獨立觀眾"),
    "returning_viewers": ("returning viewers", "回訪觀眾", "回訪觀眾人數"),
    "estimated_revenue": ("estimated revenue", "your estimated revenue", "預估收益", "預估營利"),
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
    for format_string in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%d/%m/%Y"):
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
    parsed_rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        dimensions: dict[str, str] = {}
        metrics: dict[str, float] = {}
        event_date: str | None = None
        video_id: str | None = None
        video_title: str | None = None
        for header in headers:
            value = str(raw.get(header) or "").strip()
            canonical = mapping.get(header, "")
            if canonical == "event_date":
                event_date = normalize_report_date(value)
            elif canonical == "video_id":
                video_id = value[:100] or None
            elif canonical == "video_title":
                video_title = value[:500] or None
            elif canonical in CREATOR_METRICS:
                numeric = parse_average_duration(value) if canonical == "average_view_duration_seconds" else parse_number(value)
                if numeric is not None:
                    metrics[canonical] = numeric
            elif value:
                dimensions[header[:120]] = value[:500]
        if not metrics:
            continue
        identity = {
            "date": event_date,
            "video_id": video_id,
            "video_title": None if video_id else video_title,
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
            "video_id": video_id,
            "video_title": video_title,
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
            if label.casefold().replace(" ", "") in {"台v", "台vtuber"}:
                pattern = MATCHERS[0][1]
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


def classify_content_labels(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
    live_state: str | None = None,
) -> list[str]:
    tag_values = [str(tag) for tag in tags]
    title_and_tags = " ".join([title, *tag_values])
    description_excerpt = description[:600]
    live_format = live_state in {"live", "upcoming", "completed"}
    labels: list[str] = []

    def add(label: str) -> None:
        if label not in labels:
            labels.append(label)

    # Main topic is chosen independently from format and collaboration. Title is
    # authoritative, tags are supporting evidence, and reusable descriptions are
    # only a final fallback. Event tags are deliberately ignored because channels
    # often reuse #debut / #初配信 on unrelated uploads.
    event_signal = CONTENT_PATTERNS["紀念／重大活動"].search(title)
    asmr_signal = CONTENT_PATTERNS["ASMR"].search(title_and_tags)
    song_signal = CONTENT_PATTERNS["歌回"].search(title_and_tags)
    music_signal = CONTENT_PATTERNS["音樂作品"].search(title_and_tags)
    chat_signal = CONTENT_PATTERNS["雜談"].search(title_and_tags)
    game_signal = CONTENT_PATTERNS["遊戲"].search(title_and_tags)

    if event_signal:
        add("紀念／重大活動")
        if live_format and (song_signal or music_signal):
            add("歌回")
        elif not live_format and music_signal:
            add("音樂作品")
    elif asmr_signal:
        add("ASMR")
    elif live_format and (song_signal or music_signal):
        add("歌回")
    elif not live_format and music_signal:
        add("音樂作品")
    elif song_signal:
        add("歌回")
    elif chat_signal:
        add("雜談")
    elif str(category_id or "") == "20" or game_signal:
        add("遊戲")

    # Do not use collaboration wording from descriptions: phrases such as
    # "除非合作請勿提及其他頻道" and business contact boilerplate are common.
    if not labels:
        for label in ("紀念／重大活動", "ASMR", "歌回" if live_format else "音樂作品", "雜談", "遊戲"):
            if CONTENT_PATTERNS[label].search(description_excerpt):
                add(label)
                break

    if not labels:
        add("其他")
    collaboration_tags = {"聯動", "联动", "コラボ", "collab", "collaboration"}
    if CONTENT_PATTERNS["聯動"].search(title) or any(
        tag.strip().lstrip("#＃").lower() in collaboration_tags for tag in tag_values
    ):
        add("聯動")
    return labels


def classify_content_fields(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
    live_state: str | None = None,
) -> str:
    return classify_content_labels(title, description, tags, category_id, live_state)[0]


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
                  video_id TEXT,
                  video_title TEXT,
                  views REAL,
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
            classifier_row = self.connection.execute(
                "SELECT value FROM app_settings WHERE key='content_classifier_version'"
            ).fetchone()
            try:
                classifier_version = int(json.loads(classifier_row["value"])) if classifier_row else 0
            except (TypeError, ValueError, json.JSONDecodeError):
                classifier_version = 0
            if content_type_added or content_tags_added or classifier_version < 3:
                existing_videos = self.connection.execute(
                    "SELECT video_id,title,description,category_id,tags,live_state FROM videos"
                ).fetchall()
                self.connection.executemany(
                    "UPDATE videos SET content_type=?,content_tags=? WHERE video_id=?",
                    [
                        (
                            (labels := classify_content_labels(
                                row["title"], row["description"] or "",
                                parse_json_list(row["tags"]), row["category_id"], row["live_state"]
                            ))[0],
                            json.dumps(labels, ensure_ascii=False),
                            row["video_id"],
                        )
                        for row in existing_videos
                    ],
                )
                now = utc_now()
                self.connection.execute(
                    """INSERT INTO app_settings(key,value,updated_at) VALUES ('content_classifier_version','3',?)
                       ON CONFLICT(key) DO UPDATE SET value='3',updated_at=excluded.updated_at""",
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
                self.connection.execute(
                    """INSERT INTO creator_analytics_rows (
                         batch_id,channel_id,report_name,event_date,video_id,video_title,
                         views,watch_time_hours,average_view_duration_seconds,
                         average_percentage_viewed,impressions,impressions_ctr,subscribers_net,
                         subscribers_gained,subscribers_lost,likes,comments,shares,
                         unique_viewers,returning_viewers,estimated_revenue,dimensions_json,
                         natural_key,row_hash,conflict_status,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        batch_id, channel_id, row["report_name"], row["event_date"],
                        row["video_id"], row["video_title"],
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
        content_labels = classify_content_labels(
            snippet.get("title", item["id"]),
            snippet.get("description", ""),
            tags,
            snippet.get("categoryId"),
            live_state,
        )
        content_type = content_labels[0]
        now = utc_now()
        self.execute(
            """
            INSERT INTO videos (
              video_id,channel_id,title,description,thumbnail_url,published_at,duration_seconds,
              category_id,tags,content_type,content_tags,view_count,like_count,comment_count,scheduled_start,
              actual_start,actual_end,live_state,current_concurrent,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
              title=excluded.title, description=excluded.description, thumbnail_url=excluded.thumbnail_url,
              duration_seconds=excluded.duration_seconds, category_id=excluded.category_id,
              tags=excluded.tags, content_type=excluded.content_type, content_tags=excluded.content_tags,
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
                json.dumps(tags, ensure_ascii=False), content_type,
                json.dumps(content_labels, ensure_ascii=False),
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
            raise RuntimeError(f"YouTube API {error.code}: {detail[:400]}") from error
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
        self.discovery_progress: dict[str, Any] = {
            "status": "idle",
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
        with self.state_lock:
            self.discovery_progress = {
                "status": "running",
                "started_at": utc_now(),
                "completed_at": None,
                "current_term": terms[0] if terms else None,
                "term_index": 0,
                "total_terms": len(terms),
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
                "message": "準備搜尋候選頻道",
                "error": None,
            }

    def _update_discovery(self, **values: Any) -> None:
        with self.state_lock:
            self.discovery_progress.update(values)

    def _finish_discovery(self, status: str = "completed", error: str | None = None) -> None:
        with self.state_lock:
            self.discovery_progress.update({
                "status": status,
                "completed_at": utc_now(),
                "current_term": None,
                "current_page": 0,
                "message": "探索完成" if status == "completed" else "探索未完成",
                "error": error,
            })

    def _record_error(self, error: Exception) -> None:
        with self.state_lock:
            self.last_error = str(error)[:700]
            if self.current_job == "discover" or self.discovery_progress.get("status") == "running":
                self.discovery_progress.update({
                    "status": "error",
                    "completed_at": utc_now(),
                    "message": "探索未完成",
                    "error": self.last_error,
                })

    def launch_job(self, name: str, callback: Callable[[], None]) -> tuple[bool, str]:
        if not self.config.api_key:
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

    def discover_channels(self) -> None:
        settings = self.settings_payload()
        if self.discovery_progress.get("status") != "running":
            self._begin_discovery()
        candidate_ids: set[str] = set()
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
                    if channel_id:
                        candidate_ids.add(channel_id)
                self._update_discovery(candidate_count=len(candidate_ids))
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        self._update_discovery(message=f"找到 {len(candidate_ids)} 個候選，正在核對頻道資料")
        for group in chunks(sorted(candidate_ids)):
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                with self.state_lock:
                    examined = int(self.discovery_progress["examined_count"]) + 1
                self._update_discovery(examined_count=examined)
                if self.database.is_excluded(item["id"]):
                    with self.state_lock:
                        count = int(self.discovery_progress["excluded_count"]) + 1
                    self._update_discovery(excluded_count=count)
                    continue
                evidence = evidence_for_terms(item, settings["discovery_terms"])
                if not evidence:
                    with self.state_lock:
                        count = int(self.discovery_progress["rejected_count"]) + 1
                    self._update_discovery(rejected_count=count)
                    continue
                statistics = item.get("statistics", {})
                hidden = bool(statistics.get("hiddenSubscriberCount"))
                subscribers = int(statistics["subscriberCount"]) if statistics.get("subscriberCount") else None
                if hidden or subscribers is None:
                    status = "review"
                elif subscribers >= settings["min_subscribers"]:
                    status = "eligible"
                else:
                    status = "below_threshold"
                previous = self.database.rows(
                    "SELECT discovery_status FROM channels WHERE channel_id=?", (item["id"],)
                )
                self.database.upsert_channel(item, status=status, evidence=evidence)
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
        return {
            "filename": str(payload.get("filename", ""))[:240],
            "report_count": len(reports),
            "reports": [{
                "report_name": report["report_name"],
                "row_count": len(report["rows"]),
                "recognized_metrics": report["recognized_metrics"],
            } for report in reports],
            "row_count": len(rows),
            "recognized_metrics": metrics,
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
            "imports": imports,
            "imported_overview": imported_overview,
            "overview_sources": overview_sources,
            "recent_rows": recent_rows,
            "manual_metrics": manual_metrics,
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
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")

        videos: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        for analysis_group in chunks(analysis_ids, 400):
            placeholders = ",".join("?" for _ in analysis_group)
            videos.extend(self.database.rows(
                f"""SELECT v.video_id,v.channel_id,v.title,v.description,v.thumbnail_url,
                           v.published_at,v.duration_seconds,v.category_id,v.tags,v.content_type,v.content_tags,
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
                "attributes": [
                    label for label in video["content_labels"]
                    if label != (video["content_type"] or "其他")
                ],
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
            return {
                "content_type": label,
                "description": CONTENT_TYPE_DESCRIPTIONS.get(label, CONTENT_TYPE_DESCRIPTIONS["其他"]),
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
                }
                for block in range(6)
            ]
            for day in range(7)
        ]
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
        days: int = 30,
        min_subscribers: int = 0,
        max_subscribers: int = 100_000_000,
        category: str | None = None,
        reference_channel_id: str | None = None,
        channel_ids: list[str] | None = None,
        comparison_ids: list[str] | None = None,
        format_type: str = "主要內容",
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
                if label != (video.get("content_type") or "其他")
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

        def channel_metrics(channel: dict[str, Any]) -> dict[str, Any]:
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
            return {
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
        for video in videos:
            published = published_time(video)
            if not published or published < now - timedelta(days=30):
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
        for row in cohort_metrics:
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
            (parse_api_time(row["captured_at"]) for row in snapshots if parse_api_time(row["captured_at"])),
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
                "format_type": format_type, "include_graduated": include_graduated,
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

    def scan_due_uploads(self, limit: int = 10) -> None:
        upload_scan_hours = self.settings_payload()["upload_scan_hours"]
        cutoff = (datetime.now(UTC) - timedelta(hours=upload_scan_hours)).isoformat(timespec="seconds")
        due = self.database.rows(
            """SELECT channel_id,uploads_playlist_id FROM channels
               WHERE discovery_status IN ('eligible','owned') AND uploads_playlist_id IS NOT NULL
                 AND (last_upload_scan_at IS NULL OR last_upload_scan_at < ?)
               ORDER BY COALESCE(last_upload_scan_at,'') ASC LIMIT ?""",
            (cutoff, limit),
        )
        video_ids: list[str] = []
        for channel in due:
            payload = self.youtube.get("playlistItems", {
                "part": "contentDetails,snippet", "playlistId": channel["uploads_playlist_id"],
                "maxResults": 10,
            })
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

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

    def do_OPTIONS(self) -> None:
        self._headers(204)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route == "/api/summary":
            self._json(self.service.summary())
        elif route == "/api/creator":
            query = urllib.parse.parse_qs(parsed.query)
            self._json(self.service.creator_dashboard(query.get("channel_id", [None])[0]))
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
                    days=int(query.get("days", ["30"])[0]),
                    min_subscribers=int(query.get("min_subscribers", ["0"])[0]),
                    max_subscribers=int(query.get("max_subscribers", ["100000000"])[0]),
                    category=query.get("category", [None])[0],
                    reference_channel_id=query.get("reference_channel_id", [None])[0],
                    channel_ids=[value for value in query.get("channel_ids", [""])[0].split(",") if value],
                    comparison_ids=[value for value in query.get("comparison_ids", [""])[0].split(",") if value],
                    format_type=query.get("format_type", ["主要內容"])[0],
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
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/discover":
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
        route = urllib.parse.urlparse(self.path).path
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
