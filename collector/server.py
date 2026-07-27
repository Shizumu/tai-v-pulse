from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
    retention_days = env_int("RETENTION_DAYS", 30, 1)
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
    "live_poll_seconds": (30, 3_600),
    "channel_refresh_hours": (1, 168),
    "upload_scan_hours": (1, 168),
    "retention_days": (1, 1_095),
}


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


CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("紀念／重大活動", re.compile(r"生日|周年|週年|紀念|新衣|新裝|3D|初配信|初直播|debut", re.I)),
    ("ASMR", re.compile(r"\bASMR\b|耳かき|助眠|掏耳", re.I)),
    ("音樂作品", re.compile(r"翻唱|原創曲|原创曲|original\s*song|歌ってみた|cover|music\s*video|\bMV\b", re.I)),
    ("歌回", re.compile(r"歌回|歌枠|歌唱|唱歌|karaoke|singing", re.I)),
    ("聯動", re.compile(r"聯動|联动|合作|collab|collaboration|コラボ", re.I)),
    ("雜談", re.compile(r"雜談|杂谈|聊天|閒聊|闲聊|zatsudan|雑談|free\s*talk", re.I)),
    ("遊戲", re.compile(r"遊戲|游戏|實況|实况|gameplay|gaming|プレイ", re.I)),
)

KEYWORD_STOPWORDS = {
    "vtuber", "台v", "直播", "實況", "游戏", "遊戲", "live", "stream",
    "shorts", "short", "精華", "剪輯", "中文", "台灣", "taiwan", "合作",
}


def classify_content_fields(
    title: str,
    description: str = "",
    tags: Iterable[str] = (),
    category_id: str | None = None,
) -> str:
    text = " ".join([title, description, *[str(tag) for tag in tags]])
    for label, pattern in CONTENT_PATTERNS:
        if pattern.search(text):
            return label
    if str(category_id or "") == "20":
        return "遊戲"
    return "其他"


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
                """
            )
            channel_columns = {
                row["name"] for row in self.connection.execute("PRAGMA table_info(channels)").fetchall()
            }
            if "category" not in channel_columns:
                self.connection.execute(
                    "ALTER TABLE channels ADD COLUMN category TEXT NOT NULL DEFAULT '未分類'"
                )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS channels_category_idx ON channels(category, subscriber_count DESC)"
            )
            video_columns = {
                row["name"] for row in self.connection.execute("PRAGMA table_info(videos)").fetchall()
            }
            content_type_added = "content_type" not in video_columns
            if "tags" not in video_columns:
                self.connection.execute("ALTER TABLE videos ADD COLUMN tags TEXT")
            if content_type_added:
                self.connection.execute(
                    "ALTER TABLE videos ADD COLUMN content_type TEXT NOT NULL DEFAULT '其他'"
                )
                existing_videos = self.connection.execute(
                    "SELECT video_id,title,description,category_id FROM videos"
                ).fetchall()
                self.connection.executemany(
                    "UPDATE videos SET content_type=? WHERE video_id=?",
                    [
                        (
                            classify_content_fields(
                                row["title"], row["description"] or "", (), row["category_id"]
                            ),
                            row["video_id"],
                        )
                        for row in existing_videos
                    ],
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

    def update_channel_category(self, channel_id: str, category: str) -> dict[str, Any] | None:
        category = category.strip() or "未分類"
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE channels SET category=? WHERE channel_id=?",
                (category, channel_id),
            )
            self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return {"channel_id": channel_id, "category": category}

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
        if status == "eligible" or (status is None and self.scalar(
            "SELECT discovery_status FROM channels WHERE channel_id=?", (item["id"],)
        ) == "eligible"):
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
        content_type = classify_content_fields(
            snippet.get("title", item["id"]),
            snippet.get("description", ""),
            tags,
            snippet.get("categoryId"),
        )
        now = utc_now()
        self.execute(
            """
            INSERT INTO videos (
              video_id,channel_id,title,description,thumbnail_url,published_at,duration_seconds,
              category_id,tags,content_type,view_count,like_count,comment_count,scheduled_start,
              actual_start,actual_end,live_state,current_concurrent,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
              title=excluded.title, description=excluded.description, thumbnail_url=excluded.thumbnail_url,
              duration_seconds=excluded.duration_seconds, category_id=excluded.category_id,
              tags=excluded.tags, content_type=excluded.content_type,
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

    def reserve(self, bucket: str, units: int = 1) -> None:
        limit = self.config.quota_search_limit if bucket == "search" else self.config.quota_general_limit
        safety = max(1, int(limit * self.config.quota_safety_percent / 100))
        if self.usage(bucket) + units > limit - safety:
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
        self.database = Database(config.database_path)
        self.youtube = YouTubeClient(config, self.database)
        self.run_lock = threading.Lock()
        self.state_lock = threading.RLock()
        defaults = {
            "min_subscribers": getattr(config, "min_subscribers", 1000),
            "live_poll_seconds": getattr(config, "live_poll_seconds", 60),
            "channel_refresh_hours": getattr(config, "channel_refresh_hours", 6),
            "upload_scan_hours": getattr(config, "upload_scan_hours", 4),
            "retention_days": getattr(config, "retention_days", 30),
            "discovery_terms": list(SEARCH_TERMS),
        }
        self.runtime_settings = {
            key: self.database.get_setting(key, value) for key, value in defaults.items()
        }
        self.current_job: str | None = None
        self.last_error: str | None = None
        self.running = True
        self.last_live_poll = 0.0
        self.last_upload_dispatch = 0.0
        self.last_channel_refresh = 0.0
        self.last_cleanup = 0.0

    def settings_payload(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "min_subscribers": int(self.runtime_settings["min_subscribers"]),
                "live_poll_seconds": int(self.runtime_settings["live_poll_seconds"]),
                "channel_refresh_hours": int(self.runtime_settings["channel_refresh_hours"]),
                "upload_scan_hours": int(self.runtime_settings["upload_scan_hours"]),
                "retention_days": int(self.runtime_settings["retention_days"]),
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
        threading.Thread(target=self._scheduler, name="tracker-scheduler", daemon=True).start()

    def _set_job(self, job: str | None) -> None:
        with self.state_lock:
            self.current_job = job

    def _record_error(self, error: Exception) -> None:
        with self.state_lock:
            self.last_error = str(error)[:700]

    def launch_job(self, name: str, callback: Callable[[], None]) -> tuple[bool, str]:
        if not self.config.api_key:
            return False, "尚未設定 YOUTUBE_API_KEY"
        with self.state_lock:
            if self.current_job:
                return False, f"目前正在執行：{self.current_job}"
            self.current_job = name
            self.last_error = None

        def runner() -> None:
            try:
                with self.run_lock:
                    callback()
            except Exception as error:  # background task must remain alive
                self._record_error(error)
            finally:
                self._set_job(None)

        threading.Thread(target=runner, name=f"tracker-{name}", daemon=True).start()
        return True, "已開始搜尋候選頻道" if name == "discover" else "工作已開始"

    def discover_channels(self) -> None:
        settings = self.settings_payload()
        candidate_ids: set[str] = set()
        for term in settings["discovery_terms"]:
            page_token: str | None = None
            for _ in range(self.config.discovery_pages_per_term):
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
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        for group in chunks(sorted(candidate_ids)):
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                if self.database.is_excluded(item["id"]):
                    continue
                evidence = evidence_for_terms(item, settings["discovery_terms"])
                if not evidence:
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
                self.database.upsert_channel(item, status=status, evidence=evidence)

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
                    "SELECT 1 FROM channels WHERE channel_id=? AND discovery_status='eligible'", (item["id"],)
                )),
            })
        return candidates

    def add_manual_channel(self, channel_id: str) -> dict[str, Any]:
        min_subscribers = self.settings_payload()["min_subscribers"]
        payload = self.youtube.get("channels", {
            "part": "snippet,statistics,contentDetails,brandingSettings", "id": channel_id,
        })
        items = payload.get("items", [])
        if not items:
            raise ValueError("找不到指定的 YouTube 頻道")
        item = items[0]
        statistics = item.get("statistics", {})
        if statistics.get("hiddenSubscriberCount"):
            raise ValueError(f"此頻道隱藏訂閱數，無法確認是否達到 {min_subscribers:,} 訂閱")
        subscribers = int(statistics["subscriberCount"]) if statistics.get("subscriberCount") else 0
        if subscribers < min_subscribers:
            raise ValueError(f"此頻道目前只有 {subscribers:,} 訂閱，未達收錄門檻")
        self.database.restore_excluded(channel_id)
        snippet = item.get("snippet", {})
        evidence = ("手動指定", "指定搜尋", snippet.get("title", channel_id))
        self.database.upsert_channel(item, status="eligible", evidence=evidence)
        return {"channel_id": channel_id, "title": snippet.get("title", channel_id)}

    def exclude_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.database.exclude_channel(channel_id)
        if not channel:
            raise ValueError("找不到要排除的頻道")
        return channel

    def update_channel_category(self, channel_id: str, category: str) -> dict[str, Any]:
        value = category.strip() or "未分類"
        if len(value) > 40:
            raise ValueError("分類名稱最多 40 個字")
        channel = self.database.update_channel_category(channel_id, value)
        if not channel:
            raise ValueError("找不到要分類的頻道")
        return channel

    def channel_detail(self, channel_id: str) -> dict[str, Any]:
        channels = self.database.rows(
            """SELECT channel_id,title,handle,description,keywords,country,thumbnail_url,
                      subscriber_count,view_count,video_count,hidden_subscriber_count,category,
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
        return {
            "channel": channels[0],
            "snapshots": snapshots,
            "videos": videos,
            "peak_concurrent": peak,
            "concurrency_sample_count": sum(int(video["concurrency_samples"] or 0) for video in videos),
        }

    def insights(
        self,
        days: int = 30,
        min_subscribers: int = 0,
        max_subscribers: int = 10_000_000,
        category: str | None = None,
        reference_channel_id: str | None = None,
    ) -> dict[str, Any]:
        days = max(1, min(365, int(days)))
        min_subscribers = max(0, int(min_subscribers))
        max_subscribers = max(min_subscribers, min(100_000_000, int(max_subscribers)))
        params: list[Any] = [min_subscribers, max_subscribers]
        category_sql = ""
        if category and category != "全部":
            category_sql = " AND category=?"
            params.append(category)
        cohort_channels = self.database.rows(
            f"""SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                       video_count,category,updated_at
                FROM channels WHERE discovery_status='eligible'
                  AND COALESCE(subscriber_count,0) BETWEEN ? AND ?{category_sql}
                ORDER BY subscriber_count DESC""",
            tuple(params),
        )
        reference_rows = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,
                      video_count,category,updated_at
               FROM channels WHERE channel_id=? AND discovery_status='eligible'""",
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
                           v.published_at,v.duration_seconds,v.category_id,v.tags,v.content_type,
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
        content_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        format_counter: Counter[str] = Counter()
        keyword_counter: Counter[str] = Counter()
        schedule_counts = [[0 for _ in range(6)] for _ in range(7)]
        schedule_peaks: list[list[list[int]]] = [[[] for _ in range(6)] for _ in range(7)]
        ranked_videos: list[dict[str, Any]] = []

        for video in cohort_videos:
            content_groups[video["content_type"] or "其他"].append(video)
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
                "content_type": video["content_type"] or "其他",
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

        content_breakdown: list[dict[str, Any]] = []
        for label, items in content_groups.items():
            views = [item["view_count"] for item in items if item["view_count"] is not None]
            rates: list[float] = []
            peaks = [item["peak_concurrent"] for item in items if item["peak_concurrent"] is not None]
            durations = [item["duration_seconds"] for item in items if item["duration_seconds"] is not None]
            for item in items:
                subscribers = int(channel_lookup.get(item["channel_id"], {}).get("subscriber_count") or 0)
                if subscribers and item["view_count"] is not None:
                    rates.append(float(item["view_count"]) / subscribers * 100)
            content_breakdown.append({
                "content_type": label,
                "items": len(items),
                "share": len(items) / len(cohort_videos) * 100 if cohort_videos else 0,
                "streams": sum(1 for item in items if item["format_type"] == "直播"),
                "median_views": percentile(views, .5),
                "median_view_rate": percentile(rates, .5),
                "median_peak_concurrent": percentile(peaks, .5),
                "average_duration_seconds": sum(durations) / len(durations) if durations else None,
            })
        content_breakdown.sort(key=lambda row: row["items"], reverse=True)

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
        growth_covered = sum(1 for row in cohort_metrics if row["snapshot_count"] >= 2)
        classified = sum(1 for video in cohort_videos if video["content_type"] != "其他")
        live_seconds = sum(
            int(video["duration_seconds"] or 0) for video in cohort_videos if video["format_type"] == "直播"
        )

        return {
            "period_days": days,
            "filters": {
                "min_subscribers": min_subscribers,
                "max_subscribers": max_subscribers,
                "category": category or "全部",
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
            "format_breakdown": [
                {"format_type": label, "items": count} for label, count in format_counter.most_common()
            ],
            "schedule": schedule,
            "top_videos": ranked_videos[:12],
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

    def refresh_channels(self) -> None:
        ids = [row["channel_id"] for row in self.database.rows(
            "SELECT channel_id FROM channels WHERE discovery_status IN ('eligible','review')"
        )]
        for group in chunks(ids):
            payload = self.youtube.get("channels", {
                "part": "snippet,statistics,contentDetails,brandingSettings",
                "id": ",".join(group), "maxResults": 50,
            })
            for item in payload.get("items", []):
                self.database.upsert_channel(item)

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
               WHERE discovery_status='eligible' AND uploads_playlist_id IS NOT NULL
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
        retention_days = self.settings_payload()["retention_days"]
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        self.database.execute("DELETE FROM concurrency_samples WHERE captured_at < ?", (cutoff,))
        self.database.execute("DELETE FROM channel_snapshots WHERE captured_at < ?", (cutoff,))
        self.database.execute("DELETE FROM video_snapshots WHERE captured_at < ?", (cutoff,))

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
            if now - self.last_cleanup >= 86400:
                self.cleanup()
                self.last_cleanup = now
            time.sleep(3)

    def summary(self) -> dict[str, Any]:
        settings = self.settings_payload()
        channels = self.database.rows(
            """SELECT channel_id,title,handle,thumbnail_url,subscriber_count,view_count,video_count,
                      category,match_term,match_field,match_excerpt,updated_at
               FROM channels WHERE discovery_status='eligible'
               ORDER BY subscriber_count DESC LIMIT 500"""
        )
        live_videos = self.database.rows(
            """SELECT v.video_id,v.title,c.title AS channel_title,v.thumbnail_url,v.live_state,
                      v.current_concurrent,v.scheduled_start,v.actual_start,v.updated_at
               FROM videos v JOIN channels c ON c.channel_id=v.channel_id
               WHERE v.live_state IN ('live','upcoming')
               ORDER BY CASE v.live_state WHEN 'live' THEN 0 ELSE 1 END,
                        COALESCE(v.current_concurrent,0) DESC, v.scheduled_start ASC LIMIT 100"""
        )
        with self.state_lock:
            job = self.current_job
            error = self.last_error
        return {
            "api_key_configured": bool(self.config.api_key),
            "collector_running": self.running,
            "current_job": job,
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
            "quota_general": self.youtube.usage("general"),
            "quota_search": self.youtube.usage("search"),
            "quota_general_limit": self.config.quota_general_limit,
            "quota_search_limit": self.config.quota_search_limit,
            "retention_days": settings["retention_days"],
            "settings": settings,
            "categories": self.database.rows(
                """SELECT category,COUNT(*) AS channel_count FROM channels
                   WHERE discovery_status='eligible' GROUP BY category ORDER BY category"""
            ),
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
        elif route == "/api/insights":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                payload = self.service.insights(
                    days=int(query.get("days", ["30"])[0]),
                    min_subscribers=int(query.get("min_subscribers", ["0"])[0]),
                    max_subscribers=int(query.get("max_subscribers", ["10000000"])[0]),
                    category=query.get("category", [None])[0],
                    reference_channel_id=query.get("reference_channel_id", [None])[0],
                )
                self._json(payload)
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
            prefix = "/api/channels/"
            if route.startswith(prefix):
                channel_id = urllib.parse.unquote(route[len(prefix):]).strip()
                channel = self.service.update_channel_category(
                    channel_id, str(body.get("category", ""))
                )
                self._json({"message": "頻道分類已儲存", "channel": channel})
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
