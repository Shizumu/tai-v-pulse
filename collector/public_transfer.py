from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PACKAGE_KIND = "tai-v-pulse-public-monitoring"
FORMAT_VERSION = 1
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 4 * 1024 * 1024

ROOT = Path(__file__).resolve().parents[1]

CORE_TABLES = (
    "channels",
    "channel_snapshots",
    "videos",
    "video_snapshots",
    "concurrency_samples",
    "video_classification_overrides",
)

TABLE_PATHS = {
    "channels": "data/channels.jsonl",
    "channel_snapshots": "data/channel_snapshots.jsonl",
    "videos": "data/videos.jsonl",
    "video_snapshots": "data/video_snapshots.jsonl",
    "concurrency_samples": "data/concurrency_samples.jsonl",
    "video_classification_overrides": "data/video_classification_overrides.jsonl",
    "excluded_channels": "data/excluded_channels.jsonl",
}

CHANNEL_COLUMNS = (
    "channel_id", "title", "handle", "description", "keywords", "country",
    "thumbnail_url", "subscriber_count", "view_count", "video_count",
    "hidden_subscriber_count", "uploads_playlist_id", "category",
    "organization_name", "manual_tags", "activity_status", "activity_status_source",
    "activity_status_confidence", "activity_status_reason", "activity_status_detected_at",
    "activity_status_reviewed_at", "activity_status_manual_lock", "last_activity_at",
    "created_at", "updated_at", "last_stats_at", "last_upload_scan_at",
)
CHANNEL_EVIDENCE_COLUMNS = ("match_term", "match_field", "match_excerpt")

TABLE_COLUMNS = {
    "channel_snapshots": (
        "channel_id", "captured_at", "subscriber_count", "view_count", "video_count",
    ),
    "videos": (
        "video_id", "channel_id", "title", "description", "thumbnail_url", "published_at",
        "duration_seconds", "category_id", "tags", "content_type", "content_tags",
        "classification_source", "classification_evidence", "game_name", "view_count",
        "like_count", "comment_count", "scheduled_start", "actual_start", "actual_end",
        "live_state", "current_concurrent", "updated_at",
    ),
    "video_snapshots": (
        "video_id", "captured_at", "view_count", "like_count", "comment_count",
    ),
    "concurrency_samples": ("video_id", "captured_at", "concurrent_viewers"),
    "video_classification_overrides": (
        "video_id", "content_topics", "game_name", "note", "updated_at",
    ),
    "excluded_channels": ("channel_id", "title", "reason", "excluded_at"),
}

TABLE_LABELS = {
    "channels": "已收錄頻道",
    "channel_snapshots": "頻道快照",
    "videos": "影片",
    "video_snapshots": "影片快照",
    "concurrency_samples": "直播同接樣本",
    "video_classification_overrides": "分類／遊戲人工修正",
    "excluded_channels": "黑名單",
}

JSON_LIST_COLUMNS = {
    ("channels", "manual_tags"),
    ("videos", "tags"),
    ("videos", "content_tags"),
    ("video_classification_overrides", "content_topics"),
}

NULLABLE_JSON_LIST_COLUMNS = {
    ("videos", "tags"),
}

REQUIRED_TEXT_COLUMNS = {
    "channels": ("channel_id", "title", "created_at", "updated_at"),
    "channel_snapshots": ("channel_id", "captured_at"),
    "videos": ("video_id", "channel_id", "title", "updated_at"),
    "video_snapshots": ("video_id", "captured_at"),
    "concurrency_samples": ("video_id", "captured_at"),
    "video_classification_overrides": ("video_id", "content_topics", "updated_at"),
    "excluded_channels": ("channel_id", "title", "excluded_at"),
}

TIMESTAMP_COLUMNS = {
    "channels": (
        "activity_status_detected_at", "activity_status_reviewed_at", "last_activity_at",
        "created_at", "updated_at", "last_stats_at", "last_upload_scan_at",
    ),
    "channel_snapshots": ("captured_at",),
    "videos": (
        "published_at", "scheduled_start", "actual_start", "actual_end", "updated_at",
    ),
    "video_snapshots": ("captured_at",),
    "concurrency_samples": ("captured_at",),
    "video_classification_overrides": ("updated_at",),
    "excluded_channels": ("excluded_at",),
}


@dataclass
class Inspection:
    manifest: dict[str, Any]
    preview: dict[str, Any]
    channel_ids: set[str]
    video_ids: set[str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app_version() -> str:
    try:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        return str(package.get("version") or "unknown")
    except (OSError, ValueError, TypeError):
        return "unknown"


def _columns_for(table: str, include_source_evidence: bool) -> tuple[str, ...]:
    if table == "channels":
        return CHANNEL_COLUMNS + (CHANNEL_EVIDENCE_COLUMNS if include_source_evidence else ())
    return TABLE_COLUMNS[table]


def _select_sql(table: str, columns: tuple[str, ...]) -> str:
    names = ",".join(f"t.{column}" for column in columns)
    if table == "channels":
        return f"SELECT {names} FROM channels t WHERE t.discovery_status='eligible' ORDER BY t.channel_id"
    if table == "channel_snapshots":
        return (
            f"SELECT {names} FROM channel_snapshots t JOIN channels c ON c.channel_id=t.channel_id "
            "WHERE c.discovery_status='eligible' ORDER BY t.channel_id,t.captured_at,t.id"
        )
    if table == "videos":
        return (
            f"SELECT {names} FROM videos t JOIN channels c ON c.channel_id=t.channel_id "
            "WHERE c.discovery_status='eligible' ORDER BY t.video_id"
        )
    if table in {"video_snapshots", "concurrency_samples", "video_classification_overrides"}:
        return (
            f"SELECT {names} FROM {table} t JOIN videos v ON v.video_id=t.video_id "
            "JOIN channels c ON c.channel_id=v.channel_id WHERE c.discovery_status='eligible' "
            f"ORDER BY t.video_id,{('t.captured_at,t.id' if table != 'video_classification_overrides' else 't.updated_at')}"
        )
    if table == "excluded_channels":
        return f"SELECT {names} FROM excluded_channels t ORDER BY t.channel_id"
    raise ValueError(f"不支援的資料表：{table}")


def _write_jsonl(
    archive: zipfile.ZipFile,
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    digest = hashlib.sha256()
    row_count = 0
    byte_count = 0
    with archive.open(TABLE_PATHS[table], "w") as target:
        for raw_row in connection.execute(_select_sql(table, columns)):
            row = dict(raw_row)
            encoded = (
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            target.write(encoded)
            digest.update(encoded)
            row_count += 1
            byte_count += len(encoded)
    return {
        "path": TABLE_PATHS[table],
        "columns": list(columns),
        "rows": row_count,
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def export_package(
    connection: sqlite3.Connection,
    *,
    include_blacklist: bool = False,
    include_source_evidence: bool = False,
) -> tuple[str, bytes, dict[str, Any]]:
    created_at = _utc_now()
    table_names = [*CORE_TABLES]
    if include_blacklist:
        table_names.append("excluded_channels")
    buffer = io.BytesIO()
    tables: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for table in table_names:
            columns = _columns_for(table, include_source_evidence)
            tables[table] = _write_jsonl(archive, connection, table, columns)
        manifest = {
            "package_kind": PACKAGE_KIND,
            "format_version": FORMAT_VERSION,
            "created_at": created_at,
            "producer": {"name": "台V Pulse", "version": _app_version()},
            "data_scope": "public-youtube-monitoring-only",
            "options": {
                "include_blacklist": include_blacklist,
                "include_source_evidence": include_source_evidence,
            },
            "tables": tables,
            "excluded_categories": [
                "environment-and-api-credentials",
                "oauth-client-and-token-files",
                "private-oauth-analytics",
                "studio-imports",
                "manual-private-metrics",
                "creator-workspace-selection",
                "personal-settings",
            ],
        }
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    if sum(entry["bytes"] for entry in tables.values()) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("公開監測資料解壓後超過 1 GB 安全上限，未建立不可匯入的資料包")
    package_bytes = buffer.getvalue()
    if len(package_bytes) > MAX_PACKAGE_BYTES:
        raise ValueError("公開監測資料包超過 256 MB 搬遷上限，未建立不可匯入的資料包")
    stamp = datetime.fromisoformat(created_at).strftime("%Y%m%d-%H%M%S")
    return f"tai-v-pulse-public-monitoring-{stamp}.zip", package_bytes, manifest


def _parse_timestamp(value: Any, table: str, column: str, row_number: int) -> None:
    if value is None or value == "":
        return
    if not isinstance(value, str):
        raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 不是文字時間")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 不是有效 ISO 時間"
        ) from error
    if parsed.tzinfo is None:
        raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 缺少時區")


def _validate_record(table: str, row: dict[str, Any], columns: tuple[str, ...], row_number: int) -> None:
    if set(row) != set(columns):
        missing = sorted(set(columns) - set(row))
        extra = sorted(set(row) - set(columns))
        detail = []
        if missing:
            detail.append(f"缺少 {', '.join(missing)}")
        if extra:
            detail.append(f"含未允許欄位 {', '.join(extra)}")
        raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆欄位不符：{'；'.join(detail)}")
    for column in REQUIRED_TEXT_COLUMNS[table]:
        value = row.get(column)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆缺少 {column}")
        if len(value) > 4096:
            raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 過長")
    for column in TIMESTAMP_COLUMNS[table]:
        _parse_timestamp(row.get(column), table, column, row_number)
    for json_table, column in JSON_LIST_COLUMNS:
        if json_table != table:
            continue
        value = row.get(column)
        if value is None and (table, column) in NULLABLE_JSON_LIST_COLUMNS:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 不是 JSON 文字")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 不是有效 JSON"
            ) from error
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ValueError(f"{TABLE_LABELS[table]}第 {row_number} 筆的 {column} 必須是文字清單")
    if table == "concurrency_samples":
        viewers = row.get("concurrent_viewers")
        if not isinstance(viewers, int) or isinstance(viewers, bool) or viewers < 0:
            raise ValueError(f"直播同接樣本第 {row_number} 筆的 concurrent_viewers 無效")


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        info = archive.getinfo("manifest.json")
    except KeyError as error:
        raise ValueError("資料包缺少 manifest.json") from error
    if info.file_size > 1024 * 1024:
        raise ValueError("manifest.json 超過 1 MB")
    try:
        manifest = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest.json 不是有效 UTF-8 JSON") from error
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 格式不正確")
    return manifest


def _validate_manifest(archive: zipfile.ZipFile, manifest: dict[str, Any]) -> tuple[list[str], bool]:
    if manifest.get("package_kind") != PACKAGE_KIND:
        raise ValueError("這不是台V Pulse 公開監測資料包")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            f"不支援資料格式版本 {manifest.get('format_version')}；目前只支援 {FORMAT_VERSION}"
        )
    _parse_timestamp(manifest.get("created_at"), "channels", "created_at", 0)
    options = manifest.get("options")
    if not isinstance(options, dict):
        raise ValueError("資料包缺少匯出選項")
    include_blacklist = options.get("include_blacklist")
    include_evidence = options.get("include_source_evidence")
    if not isinstance(include_blacklist, bool) or not isinstance(include_evidence, bool):
        raise ValueError("資料包匯出選項格式不正確")
    expected_tables = [*CORE_TABLES]
    if include_blacklist:
        expected_tables.append("excluded_channels")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(expected_tables):
        raise ValueError("manifest.json 的資料表白名單不完整或含未允許資料表")

    expected_members = {"manifest.json"}
    for table in expected_tables:
        entry = tables.get(table)
        if not isinstance(entry, dict):
            raise ValueError(f"manifest.json 缺少 {table} 定義")
        path = entry.get("path")
        if path != TABLE_PATHS[table]:
            raise ValueError(f"{table} 的資料路徑不正確")
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValueError("資料包含不安全的檔案路徑")
        expected_columns = _columns_for(table, include_evidence)
        if entry.get("columns") != list(expected_columns):
            raise ValueError(f"{table} 的欄位版本不相容")
        if not isinstance(entry.get("rows"), int) or entry["rows"] < 0:
            raise ValueError(f"{table} 的筆數資訊無效")
        if not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
            raise ValueError(f"{table} 的檔案大小資訊無效")
        if not re_full_sha256(entry.get("sha256")):
            raise ValueError(f"{table} 的 SHA-256 格式無效")
        expected_members.add(path)

    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("資料包含重複檔名")
    if set(names) != expected_members:
        raise ValueError("資料包含 manifest 未列出的檔案，或缺少必要檔案")
    total_uncompressed = sum(info.file_size for info in infos)
    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("資料包解壓後超過 1 GB 安全上限")
    if any(info.flag_bits & 0x1 for info in infos):
        raise ValueError("不支援加密 ZIP")
    return expected_tables, include_evidence


def re_full_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _iter_records(
    archive: zipfile.ZipFile,
    table: str,
    entry: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    columns = tuple(entry["columns"])
    digest = hashlib.sha256()
    row_count = 0
    byte_count = 0
    with archive.open(entry["path"], "r") as source:
        while True:
            line = source.readline(MAX_JSONL_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise ValueError(f"{TABLE_LABELS[table]}含超過 4 MB 的單筆資料")
            digest.update(line)
            byte_count += len(line)
            if not line.strip():
                raise ValueError(f"{TABLE_LABELS[table]}第 {row_count + 1} 筆是空白列")
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"{TABLE_LABELS[table]}第 {row_count + 1} 筆不是有效 UTF-8 JSON"
                ) from error
            if not isinstance(row, dict):
                raise ValueError(f"{TABLE_LABELS[table]}第 {row_count + 1} 筆不是物件")
            row_count += 1
            _validate_record(table, row, columns, row_count)
            yield row
    if row_count != entry["rows"]:
        raise ValueError(
            f"{TABLE_LABELS[table]}筆數不符：manifest 為 {entry['rows']}，實際為 {row_count}"
        )
    if byte_count != entry["bytes"]:
        raise ValueError(f"{TABLE_LABELS[table]}檔案大小與 manifest 不符")
    if digest.hexdigest() != entry["sha256"]:
        raise ValueError(f"{TABLE_LABELS[table]} SHA-256 完整性檢查失敗")


def _inspect_package(package_bytes: bytes) -> Inspection:
    if not package_bytes:
        raise ValueError("請先選擇公開監測資料 ZIP")
    if len(package_bytes) > MAX_PACKAGE_BYTES:
        raise ValueError("資料包超過 256 MB 上傳上限")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError("檔案不是有效 ZIP 資料包") from error
    with archive:
        manifest = _read_manifest(archive)
        expected_tables, _ = _validate_manifest(archive, manifest)
        channel_ids: set[str] = set()
        video_ids: set[str] = set()
        override_ids: set[str] = set()
        excluded_ids: set[str] = set()
        counts: dict[str, int] = {}
        for table in expected_tables:
            rows_seen = 0
            for row in _iter_records(archive, table, manifest["tables"][table]):
                rows_seen += 1
                if table == "channels":
                    channel_id = row["channel_id"]
                    if channel_id in channel_ids:
                        raise ValueError(f"已收錄頻道含重複 Channel ID：{channel_id}")
                    channel_ids.add(channel_id)
                elif table == "videos":
                    if row["channel_id"] not in channel_ids:
                        raise ValueError(f"影片 {row['video_id']} 找不到對應的已收錄頻道")
                    video_id = row["video_id"]
                    if video_id in video_ids:
                        raise ValueError(f"影片含重複 Video ID：{video_id}")
                    video_ids.add(video_id)
                elif table in {"channel_snapshots"}:
                    if row["channel_id"] not in channel_ids:
                        raise ValueError(f"頻道快照找不到對應頻道：{row['channel_id']}")
                elif table in {"video_snapshots", "concurrency_samples"}:
                    if row["video_id"] not in video_ids:
                        raise ValueError(f"{TABLE_LABELS[table]}找不到對應影片：{row['video_id']}")
                elif table == "video_classification_overrides":
                    if row["video_id"] not in video_ids:
                        raise ValueError(f"分類修正找不到對應影片：{row['video_id']}")
                    if row["video_id"] in override_ids:
                        raise ValueError(f"分類修正含重複 Video ID：{row['video_id']}")
                    override_ids.add(row["video_id"])
                elif table == "excluded_channels":
                    channel_id = row["channel_id"]
                    if channel_id in excluded_ids:
                        raise ValueError(f"黑名單含重複 Channel ID：{channel_id}")
                    if channel_id in channel_ids:
                        raise ValueError(f"頻道同時出現在收錄名單與黑名單：{channel_id}")
                    excluded_ids.add(channel_id)
            counts[table] = rows_seen

        options = manifest["options"]
        checks = [
            {"key": "format", "status": "passed", "message": "資料格式版本與白名單相容"},
            {"key": "files", "status": "passed", "message": "ZIP 只包含 manifest 列出的公開資料檔"},
            {"key": "checksums", "status": "passed", "message": "所有資料檔 SHA-256 與筆數一致"},
            {"key": "references", "status": "passed", "message": "頻道、影片、快照與人工修正關聯完整"},
            {"key": "privacy_scope", "status": "passed", "message": "格式白名單不接受憑證、私人 Analytics、Studio、手動補值或個人設定"},
        ]
        preview = {
            "valid": True,
            "package_kind": manifest["package_kind"],
            "format_version": manifest["format_version"],
            "created_at": manifest["created_at"],
            "producer_version": str((manifest.get("producer") or {}).get("version") or "unknown"),
            "options": options,
            "counts": {table: counts.get(table, 0) for table in (*CORE_TABLES, "excluded_channels")},
            "checks": checks,
            "warnings": ([
                "此資料包未包含黑名單；匯入時會保留目的端既有黑名單。"
            ] if not options["include_blacklist"] else []) + ([
                "此資料包未包含收錄來源證據；新加入頻道的來源欄位會留白，合併時不覆蓋既有證據。"
            ] if not options["include_source_evidence"] else []),
        }
        return Inspection(manifest, preview, channel_ids, video_ids)


def preview_package(package_bytes: bytes) -> dict[str, Any]:
    return _inspect_package(package_bytes).preview


def _private_channel_ids(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """SELECT channel_id FROM creator_workspace_channels
           UNION SELECT channel_id FROM creator_import_batches
           UNION SELECT channel_id FROM creator_analytics_rows
           UNION SELECT channel_id FROM creator_manual_metrics
           UNION SELECT channel_id FROM creator_oauth_connections
           UNION SELECT channel_id FROM creator_oauth_summary_metrics
           UNION SELECT channel_id FROM creator_oauth_daily_metrics
           UNION SELECT channel_id FROM creator_oauth_video_metrics"""
    ).fetchall()
    result = {str(row[0]) for row in rows if row[0]}
    owned = connection.execute(
        "SELECT value FROM app_settings WHERE key='owned_channel_id'"
    ).fetchone()
    if owned:
        try:
            value = str(json.loads(owned[0]) or "").strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            value = ""
        if value:
            result.add(value)
    return result


def _batches(records: Iterable[dict[str, Any]], size: int = 1000) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for record in records:
        batch.append(record)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _insert_channels(
    connection: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    *,
    replace: bool,
    include_source_evidence: bool,
) -> None:
    columns = _columns_for("channels", include_source_evidence)
    insert_columns = (*columns, "discovery_status")
    placeholders = ",".join("?" for _ in insert_columns)
    if replace:
        assignments = ",".join(
            f"{column}=excluded.{column}" for column in columns if column != "channel_id"
        )
    else:
        newer = "datetime(excluded.updated_at)>=datetime(channels.updated_at)"
        assignments = ",".join(
            f"{column}=CASE WHEN {newer} THEN excluded.{column} ELSE channels.{column} END"
            for column in columns if column not in {"channel_id", "created_at"}
        )
        assignments += ",created_at=CASE WHEN excluded.created_at<channels.created_at THEN excluded.created_at ELSE channels.created_at END"
    assignments += ",discovery_status='eligible'"
    sql = (
        f"INSERT INTO channels ({','.join(insert_columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(channel_id) DO UPDATE SET {assignments}"
    )
    for batch in _batches(records):
        connection.executemany(
            sql,
            [tuple(row[column] for column in columns) + ("eligible",) for row in batch],
        )


def _insert_primary_table(
    connection: sqlite3.Connection,
    table: str,
    records: Iterable[dict[str, Any]],
    *,
    replace: bool,
) -> None:
    columns = TABLE_COLUMNS[table]
    key = "video_id" if table in {"videos", "video_classification_overrides"} else "channel_id"
    placeholders = ",".join("?" for _ in columns)
    assignments = ",".join(
        f"{column}=excluded.{column}" for column in columns if column != key
    )
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {assignments}"
    )
    if not replace:
        sql += f" WHERE datetime(excluded.updated_at)>=datetime({table}.updated_at)"
    for batch in _batches(records):
        connection.executemany(sql, [tuple(row[column] for column in columns) for row in batch])


def _insert_history_table(
    connection: sqlite3.Connection,
    table: str,
    records: Iterable[dict[str, Any]],
) -> None:
    columns = TABLE_COLUMNS[table]
    parent = "channel_id" if table == "channel_snapshots" else "video_id"
    placeholders = ",".join("?" for _ in columns)
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) SELECT {placeholders} "
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {parent}=? AND captured_at=?)"
    )
    for batch in _batches(records):
        parameters = []
        for row in batch:
            values = tuple(row[column] for column in columns)
            parameters.append(values + (row[parent], row["captured_at"]))
        connection.executemany(sql, parameters)


def _apply_blacklist(
    connection: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    private_ids: set[str],
) -> None:
    columns = TABLE_COLUMNS["excluded_channels"]
    sql = (
        "INSERT INTO excluded_channels(channel_id,title,reason,excluded_at) VALUES (?,?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,reason=excluded.reason,excluded_at=excluded.excluded_at"
    )
    for batch in _batches(records):
        ids = [row["channel_id"] for row in batch]
        for channel_id in ids:
            if channel_id in private_ids:
                connection.execute(
                    "UPDATE channels SET discovery_status='owned' WHERE channel_id=?",
                    (channel_id,),
                )
            else:
                connection.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        connection.executemany(sql, [tuple(row[column] for column in columns) for row in batch])


def import_package(
    connection: sqlite3.Connection,
    package_bytes: bytes,
    mode: str,
) -> dict[str, Any]:
    if mode not in {"merge", "replace"}:
        raise ValueError("匯入方式必須是 merge 或 replace")
    inspection = _inspect_package(package_bytes)
    manifest = inspection.manifest
    include_blacklist = bool(manifest["options"]["include_blacklist"])
    include_evidence = bool(manifest["options"]["include_source_evidence"])
    private_ids = _private_channel_ids(connection)
    preserved_private_channels = 0

    archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if mode == "replace":
            connection.execute("DELETE FROM manual_refresh_queue")
            connection.execute("DELETE FROM video_classification_overrides")
            connection.execute("DELETE FROM concurrency_samples")
            connection.execute("DELETE FROM video_snapshots")
            connection.execute("DELETE FROM videos")
            connection.execute("DELETE FROM channel_snapshots")
            local_public_ids = {
                str(row[0]) for row in connection.execute(
                    "SELECT channel_id FROM channels WHERE discovery_status='eligible'"
                ).fetchall()
            }
            for channel_id in sorted(local_public_ids - inspection.channel_ids):
                if channel_id in private_ids:
                    connection.execute(
                        "UPDATE channels SET discovery_status='owned' WHERE channel_id=?",
                        (channel_id,),
                    )
                    preserved_private_channels += 1
                else:
                    connection.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
            if include_blacklist:
                connection.execute("DELETE FROM excluded_channels")

        connection.executemany(
            "DELETE FROM excluded_channels WHERE channel_id=?",
            [(channel_id,) for channel_id in sorted(inspection.channel_ids)],
        )
        if mode == "replace" and not include_evidence:
            connection.executemany(
                """UPDATE channels SET match_term=NULL,match_field=NULL,match_excerpt=NULL
                   WHERE channel_id=?""",
                [(channel_id,) for channel_id in sorted(inspection.channel_ids)],
            )

        _insert_channels(
            connection,
            _iter_records(archive, "channels", manifest["tables"]["channels"]),
            replace=mode == "replace",
            include_source_evidence=include_evidence,
        )
        _insert_primary_table(
            connection,
            "videos",
            _iter_records(archive, "videos", manifest["tables"]["videos"]),
            replace=mode == "replace",
        )
        _insert_history_table(
            connection,
            "channel_snapshots",
            _iter_records(archive, "channel_snapshots", manifest["tables"]["channel_snapshots"]),
        )
        _insert_history_table(
            connection,
            "video_snapshots",
            _iter_records(archive, "video_snapshots", manifest["tables"]["video_snapshots"]),
        )
        _insert_history_table(
            connection,
            "concurrency_samples",
            _iter_records(archive, "concurrency_samples", manifest["tables"]["concurrency_samples"]),
        )
        _insert_primary_table(
            connection,
            "video_classification_overrides",
            _iter_records(
                archive,
                "video_classification_overrides",
                manifest["tables"]["video_classification_overrides"],
            ),
            replace=mode == "replace",
        )
        if include_blacklist:
            _apply_blacklist(
                connection,
                _iter_records(
                    archive, "excluded_channels", manifest["tables"]["excluded_channels"]
                ),
                private_ids,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        archive.close()

    return {
        "mode": mode,
        "format_version": manifest["format_version"],
        "created_at": manifest["created_at"],
        "counts": inspection.preview["counts"],
        "included_blacklist": include_blacklist,
        "included_source_evidence": include_evidence,
        "preserved_private_channels": preserved_private_channels,
    }
