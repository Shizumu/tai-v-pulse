import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from email.message import Message

from collector.external_directory import parse_directory_csv
from collector.oauth import GoogleOAuth, oauth_error_guidance
from collector.server import (
    Database,
    RequestHandler,
    TrackerService,
    YouTubeAPIError,
    channel_query_target,
    classify_content_details,
    classify_content_fields,
    classify_content_labels,
    concurrency_sample_stats,
    evidence_for,
    evidence_for_terms,
    hourly_live_scan_slot,
    load_env,
    parse_duration,
    parse_studio_csv,
    utc_now,
    video_format,
)


class CollectorTests(unittest.TestCase):
    @staticmethod
    def public_channel_item(channel_id="UC-public", title="公開台V"):
        return {
            "id": channel_id,
            "snippet": {
                "title": title,
                "description": "台V 公開頻道說明",
                "customUrl": f"@{channel_id.lower()}",
                "country": "TW",
                "thumbnails": {"default": {"url": "https://example.invalid/channel.jpg"}},
            },
            "statistics": {
                "subscriberCount": "2500",
                "viewCount": "120000",
                "videoCount": "40",
            },
            "contentDetails": {"relatedPlaylists": {"uploads": f"UU-{channel_id}"}},
            "brandingSettings": {"channel": {"keywords": "台V gaming"}},
        }

    @staticmethod
    def public_video_item(video_id="video-public", channel_id="UC-public"):
        return {
            "id": video_id,
            "snippet": {
                "channelId": channel_id,
                "title": "公開測試直播",
                "description": "公開影片說明",
                "publishedAt": "2026-07-29T12:00:00Z",
                "categoryId": "20",
                "tags": ["台V", "遊戲"],
                "thumbnails": {"default": {"url": "https://example.invalid/video.jpg"}},
            },
            "statistics": {"viewCount": "1000", "likeCount": "80", "commentCount": "12"},
            "contentDetails": {"duration": "PT1H"},
            "liveStreamingDetails": {
                "actualStartTime": "2026-07-29T12:00:00Z",
                "concurrentViewers": "88",
            },
        }

    def test_external_directory_parses_only_active_taiwanese_individuals(self):
        eligible_id = "UC" + "A" * 22
        csv_text = "\n".join([
            "ID,Display Name,Alias Names,Youtube Channel ID,Twitch Channel ID,Twitch Channel Name,Debut Date,Graduation Date,Activity,Group Name,Nationality",
            "## VTuber Group Official Channels,,,,,,,,,,",
            f"1,團體官方,,{('UC' + 'G' * 22)},,,,,Active,Group,TW",
            "## Taiwanese VTubers,,,,,,,,,,",
            f"3,活動中台V,,{eligible_id},,,,,Active,Example,TW",
            f"4,重複台V,,{eligible_id},,,,,Active,,TW",
            f"5,已畢業台V,,{('UC' + 'B' * 22)},,,,2025-01-01,Graduated,,TW",
            f"6,香港V,,{('UC' + 'H' * 22)},,,,,Active,,HK",
            "7,沒有 YouTube,,,,,,,Active,,TW",
            "8,錯誤 ID,,not-a-channel,,,,,Active,,TW",
        ])
        parsed = parse_directory_csv(csv_text)
        self.assertEqual([row["channel_id"] for row in parsed["entries"]], [eligible_id])
        self.assertEqual(parsed["selected_count"], 1)
        self.assertEqual(parsed["inactive_count"], 1)
        self.assertEqual(parsed["missing_youtube_count"], 1)
        self.assertEqual(parsed["invalid_youtube_count"], 1)
        self.assertEqual(parsed["duplicate_count"], 1)

    def test_external_directory_directly_includes_eligible_channels_with_audit(self):
        eligible_id = "UC" + "A" * 22
        below_id = "UC" + "B" * 22
        excluded_id = "UC" + "C" * 22
        csv_text = "\n".join([
            "ID,Display Name,Alias Names,Youtube Channel ID,Twitch Channel ID,Twitch Channel Name,Debut Date,Graduation Date,Activity,Group Name,Nationality",
            "## Taiwanese VTubers,,,,,,,,,,",
            f"2,名錄合格台V,,{eligible_id},,,,,Active,測試社,TW",
            f"3,名錄未達門檻台V,,{below_id},,,,,Active,,TW",
            f"4,名錄黑名單台V,,{excluded_id},,,,,Active,,TW",
        ])

        def item(channel_id: str, subscribers: int) -> dict:
            value = self.public_channel_item(channel_id, f"YouTube {channel_id[-1]}")
            value["statistics"]["subscriberCount"] = str(subscribers)
            value["snippet"]["description"] = "沒有台 V 自述字樣"
            value["brandingSettings"]["channel"]["keywords"] = "gaming"
            return value

        items = {
            eligible_id: item(eligible_id, 2500),
            below_id: item(below_id, 500),
        }

        class FakeYouTube:
            def __init__(self):
                self.calls = []

            def get(self, resource, params, bucket="general"):
                self.calls.append((resource, bucket, params["id"]))
                return {
                    "items": [
                        items[channel_id]
                        for channel_id in params["id"].split(",")
                        if channel_id in items
                    ]
                }

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                discovery_pages_per_term=1,
                api_key="test",
            )
            service = TrackerService(config)
            youtube = FakeYouTube()
            service.youtube = youtube
            service.directory_fetcher = lambda: {
                "text": csv_text,
                "source_commit": "a" * 40,
                "source_sha256": "b" * 64,
            }
            service.database.execute(
                """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
                   VALUES (?,?,?,?)""",
                (excluded_id, "名錄黑名單台V", "manual", utc_now()),
            )

            result = service.import_external_directory()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["selected_count"], 3)
            self.assertEqual(result["examined_count"], 2)
            self.assertEqual(result["eligible_count"], 1)
            self.assertEqual(result["new_count"], 1)
            self.assertEqual(result["below_threshold_count"], 1)
            self.assertEqual(result["excluded_count"], 1)
            self.assertEqual(youtube.calls[0][0:2], ("channels", "general"))
            self.assertNotIn(excluded_id, youtube.calls[0][2])

            included = service.database.rows(
                """SELECT discovery_status,match_term,match_field,match_excerpt
                   FROM channels WHERE channel_id=?""",
                (eligible_id,),
            )[0]
            self.assertEqual(included["discovery_status"], "eligible")
            self.assertEqual(included["match_term"], "Taiwan VTuber Data")
            self.assertEqual(included["match_field"], "外部名錄")
            self.assertIn("名錄合格台V", included["match_excerpt"])
            self.assertEqual(service.database.scalar(
                "SELECT discovery_status FROM channels WHERE channel_id=?", (below_id,)
            ), "below_threshold")
            audit = service.database.rows(
                """SELECT channel_id,result_status FROM external_directory_entries
                   ORDER BY channel_id"""
            )
            self.assertEqual(
                {row["channel_id"]: row["result_status"] for row in audit},
                {eligible_id: "included", below_id: "below_threshold", excluded_id: "excluded"},
            )
            service.database.close()

    def test_comparison_recommendations_group_by_scale_and_rank_content_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=30,
            ))
            channels = (
                ("UC-reference", "我的頻道", 10000),
                ("UC-smaller", "小型參考", 4000),
                ("UC-peer-similar", "內容相似同級", 15000),
                ("UC-peer-scale", "規模很近但主題不同", 10100),
                ("UC-larger", "成長參考", 30000),
                ("UC-paused", "休止頻道", 32000),
            )
            for channel_id, title, subscribers in channels:
                item = self.public_channel_item(channel_id, title)
                item["statistics"]["subscriberCount"] = str(subscribers)
                service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
                service.database.execute(
                    "UPDATE channels SET activity_status='活動中', category='個人勢' WHERE channel_id=?",
                    (channel_id,),
                )
                for index in range(3):
                    video = self.public_video_item(f"video-{channel_id}-{index}", channel_id)
                    video["snippet"]["publishedAt"] = utc_now()
                    video["liveStreamingDetails"]["actualStartTime"] = utc_now()
                    if channel_id == "UC-peer-scale":
                        video["snippet"]["title"] = f"ASMR 睡前陪伴 {index}"
                        video["snippet"]["categoryId"] = "22"
                        video["snippet"]["tags"] = ["ASMR"]
                    service.database.upsert_video(video)
            service.database.execute(
                "UPDATE channels SET activity_status='休止中' WHERE channel_id='UC-paused'"
            )

            result = service.comparison_recommendations("UC-reference", per_group=2)

            self.assertEqual(result["status"], "ready")
            groups = {group["key"]: group for group in result["groups"]}
            self.assertEqual(groups["smaller"]["channels"][0]["channel_id"], "UC-smaller")
            self.assertEqual(groups["peer"]["channels"][0]["channel_id"], "UC-peer-similar")
            self.assertEqual(groups["larger"]["channels"][0]["channel_id"], "UC-larger")
            self.assertNotIn(
                "UC-paused",
                [channel["channel_id"] for group in result["groups"] for channel in group["channels"]],
            )
            self.assertIn("共同主題：遊戲", groups["peer"]["channels"][0]["reasons"])
            self.assertIn("無法確認", result["methodology"]["audience_boundary"])
            service.database.close()

    def test_concurrency_sample_stats_requires_sufficient_coverage(self):
        start = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        complete = [
            {
                "captured_at": (start + timedelta(minutes=index)).isoformat(),
                "concurrent_viewers": 100 + index,
            }
            for index in range(90)
        ]
        result = concurrency_sample_stats(
            complete,
            actual_start=start.isoformat(),
            actual_end=(start + timedelta(minutes=90)).isoformat(),
            duration_seconds=5400,
            poll_seconds=60,
        )
        self.assertTrue(result["concurrency_ready"])
        self.assertEqual(result["concurrency_sample_count"], 90)
        self.assertEqual(result["concurrency_coverage"], 100)
        self.assertAlmostEqual(result["average_concurrent"], 144.5)
        self.assertEqual(result["peak_concurrent"], 189)

        incomplete = concurrency_sample_stats(
            complete[:19],
            actual_start=start.isoformat(),
            actual_end=(start + timedelta(minutes=90)).isoformat(),
            duration_seconds=5400,
            poll_seconds=60,
        )
        self.assertFalse(incomplete["concurrency_ready"])
        self.assertIsNone(incomplete["average_concurrent"])

    def test_load_env_accepts_bom_spacing_quotes_and_last_value(self):
        key = "TAI_V_PULSE_TEST_ENV_KEY"
        ansi_key = "TAI_V_PULSE_TEST_ANSI_KEY"
        previous = os.environ.pop(key, None)
        previous_ansi = os.environ.pop(ansi_key, None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                env_path = Path(directory) / ".env"
                env_path.write_text(
                    f'\ufeff# test\n{key}=\nexport {key} = "configured-value"\n',
                    encoding="utf-8",
                )
                load_env(env_path)
                self.assertEqual(os.environ.get(key), "configured-value")

                ansi_env_path = Path(directory) / ".env.ansi"
                ansi_env_path.write_text(
                    f"# 繁體中文設定\n{ansi_key}=ansi-value\n",
                    encoding="cp950",
                )
                load_env(ansi_env_path)
                self.assertEqual(os.environ.get(ansi_key), "ansi-value")
        finally:
            os.environ.pop(key, None)
            os.environ.pop(ansi_key, None)
            if previous is not None:
                os.environ[key] = previous
            if previous_ansi is not None:
                os.environ[ansi_key] = previous_ansi

    def test_self_declaration_evidence(self):
        item = {
            "snippet": {"title": "範例頻道", "description": "來自台灣的台V，主要做遊戲直播。"},
            "brandingSettings": {"channel": {"keywords": "VTuber 遊戲"}},
        }
        evidence = evidence_for(item)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence[0], "台V")
        self.assertEqual(evidence[1], "頻道說明")

    def test_vlog_is_not_mistaken_for_tai_v(self):
        item = {
            "snippet": {"title": "台VLOG旅遊", "description": "旅行紀錄"},
            "brandingSettings": {"channel": {}},
        }
        self.assertIsNone(evidence_for(item))

    def test_discovery_evidence_normalizes_case_and_spacing_variants(self):
        tai_v_variants = ["台V", "台v", "台 V", "台 v"]
        taiwan_vtuber_variants = ["台灣VTuber", "台灣vtuber", "台灣 VTuber", "台灣 vtuber"]
        for declaration in tai_v_variants:
            item = {
                "snippet": {"title": "範例頻道", "description": declaration},
                "brandingSettings": {"channel": {}},
            }
            self.assertIsNotNone(evidence_for_terms(item, ["台V"]), declaration)
        for declaration in taiwan_vtuber_variants:
            item = {
                "snippet": {"title": "範例頻道", "description": declaration},
                "brandingSettings": {"channel": {}},
            }
            self.assertIsNotNone(evidence_for_terms(item, ["台灣VTuber"]), declaration)

    def test_custom_discovery_term_is_used_as_evidence(self):
        item = {
            "snippet": {"title": "島嶼箱庭成員", "description": "主要做遊戲直播。"},
            "brandingSettings": {"channel": {}},
        }
        evidence = evidence_for_terms(item, ["島嶼箱庭"])
        self.assertEqual(evidence, ("島嶼箱庭", "頻道名稱", "島嶼箱庭成員"))

    def test_duration_parser(self):
        self.assertEqual(parse_duration("PT2H3M4S"), 7384)

    def test_content_and_format_classification(self):
        self.assertEqual(classify_content_fields("深夜助眠 ASMR"), "ASMR")
        self.assertEqual(classify_content_fields("今晚來玩", category_id="20"), "遊戲")
        self.assertEqual(classify_content_fields("Cover 歌曲", live_state="video"), "音樂作品")
        self.assertEqual(classify_content_fields("Cover 歌回", live_state="completed"), "歌回")
        self.assertEqual(
            classify_content_labels("生日紀念歌回", live_state="completed"),
            ["紀念／重大活動", "歌回"],
        )
        self.assertEqual(
            classify_content_labels(
                "【Palworld】伊伊陪玩不收費不限會員",
                "除非合作請勿提及其他頻道；合作邀約請寄信。",
                category_id="20",
                live_state="completed",
            ),
            ["遊戲"],
        )
        self.assertEqual(
            classify_content_labels(
                "【Minecraft 聯動】和 @friend 一起蓋房子",
                category_id="20",
                live_state="completed",
            ),
            ["遊戲", "聯動"],
        )
        self.assertEqual(
            classify_content_labels(
                "【重返未來1999】全服更新原子之心聯動！",
                category_id="20",
                live_state="completed",
            ),
            ["遊戲"],
        )
        self.assertEqual(
            classify_content_fields(
                "早安台，今天一起吃早餐", tags=["singing", "歌回"],
                category_id="20", live_state="completed",
            ),
            "雜談",
        )
        self.assertEqual(
            classify_content_fields("朝活 Minecraft 初見", live_state="completed"),
            "遊戲",
        )
        self.assertEqual(
            classify_content_fields("早安歌回", tags=["聊天"], live_state="completed"),
            "歌回",
        )
        mixed = classify_content_details("深夜歌雜陪伴", live_state="completed")
        self.assertEqual(mixed["content_type"], "歌回 + 雜談")
        self.assertEqual(mixed["labels"], ["歌回", "雜談"])
        game = classify_content_details("【WARFRAME】新手開荒", live_state="completed")
        self.assertEqual(game["content_type"], "遊戲")
        self.assertEqual(game["game_name"], "Warframe")
        self.assertEqual(game["classification_source"], "標題（系統遊戲別名）")
        self.assertEqual(video_format("video", 42, "短片"), "Shorts")
        self.assertEqual(video_format("completed", 7200, "直播存檔"), "直播")
        self.assertEqual(video_format("unavailable", 7200, "已不可公開存取的直播"), "直播")

    def test_specific_channel_query_parser(self):
        channel_id = "UC1234567890abcdefghijkl"
        self.assertEqual(
            channel_query_target(f"https://www.youtube.com/channel/{channel_id}"),
            ("id", channel_id),
        )
        self.assertEqual(
            channel_query_target("https://www.youtube.com/@example.vtuber"),
            ("handle", "@example.vtuber"),
        )
        self.assertEqual(channel_query_target("杏仁ミル"), ("search", "杏仁ミル"))

    def test_hourly_live_scan_slots_use_taipei_time(self):
        self.assertEqual(
            hourly_live_scan_slot(datetime(2026, 7, 29, 16, 5, tzinfo=UTC)),
            "2026-07-30T00:05",
        )
        self.assertEqual(
            hourly_live_scan_slot(datetime(2026, 7, 29, 17, 5, tzinfo=UTC)),
            "2026-07-30T01:05",
        )
        self.assertEqual(
            hourly_live_scan_slot(datetime(2026, 7, 29, 7, 5, tzinfo=UTC)),
            "2026-07-29T15:05",
        )
        self.assertIsNone(hourly_live_scan_slot(datetime(2026, 7, 29, 9, 5, tzinfo=UTC)))
        self.assertIsNone(hourly_live_scan_slot(datetime(2026, 7, 29, 17, 0, tzinfo=UTC)))
        self.assertIsNone(hourly_live_scan_slot(datetime(2026, 7, 29, 17, 6, tzinfo=UTC)))

    def test_database_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM channels"), 0)
            columns = {row["name"] for row in database.rows("PRAGMA table_info(channels)")}
            self.assertIn("category", columns)
            self.assertIn("organization_name", columns)
            self.assertIn("manual_tags", columns)
            self.assertIn("activity_status", columns)
            self.assertIn("last_activity_at", columns)
            video_columns = {row["name"] for row in database.rows("PRAGMA table_info(videos)")}
            self.assertIn("content_tags", video_columns)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM creator_import_batches"), 0)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM creator_workspace_channels"), 0)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM manual_refresh_queue"), 0)
            database.close()

    def test_summary_keeps_recent_background_job_status(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                api_key="",
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                edition="public",
                retention_days=30,
                creator_retention_days=0,
                discovery_pages_per_term=1,
                quota_general_limit=10_000,
                quota_search_limit=100,
                quota_safety_percent=10,
            )
            service = TrackerService(config)

            service._set_job("live-poll")
            running = service.summary()
            self.assertEqual(running["current_job"], "live-poll")
            self.assertIsNotNone(running["current_job_started_at"])

            service._set_job(None)
            completed = service.summary()
            self.assertIsNone(completed["current_job"])
            self.assertEqual(completed["last_job"], "live-poll")
            self.assertEqual(completed["last_job_status"], "completed")
            self.assertIsNotNone(completed["last_job_finished_at"])
            service.database.close()

    def test_hourly_live_scan_prioritizes_owned_channel_and_preserves_full_scan_age(self):
        def channel_item(channel_id, title, uploads):
            return {
                "id": channel_id,
                "snippet": {"title": title, "description": "台V", "thumbnails": {}},
                "statistics": {"subscriberCount": "2000", "viewCount": "10000", "videoCount": "10"},
                "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
                "brandingSettings": {"channel": {}},
            }

        videos = {
            "video-owned-live": {
                "id": "video-owned-live",
                "snippet": {"channelId": "UC-owned", "title": "手動開台", "thumbnails": {}},
                "statistics": {"viewCount": "10"},
                "contentDetails": {"duration": "PT1H"},
                "liveStreamingDetails": {"actualStartTime": "2026-07-29T13:00:00Z", "concurrentViewers": "25"},
            },
            "video-peer-upcoming": {
                "id": "video-peer-upcoming",
                "snippet": {"channelId": "UC-peer", "title": "整點預定", "thumbnails": {}},
                "statistics": {"viewCount": "0"},
                "contentDetails": {"duration": "PT0S"},
                "liveStreamingDetails": {"scheduledStartTime": "2026-07-29T14:00:00Z"},
            },
        }

        class FakeYouTube:
            def __init__(self):
                self.calls = []
                self.general_usage = 0

            def usage(self, bucket):
                return self.general_usage

            def safe_limit(self, bucket):
                return 9000

            def get(self, resource, params, bucket="general"):
                self.calls.append((resource, dict(params)))
                self.general_usage += 1
                if resource == "playlistItems":
                    video_id = {
                        "UU-owned": "video-owned-live",
                        "UU-peer": "video-peer-upcoming",
                    }[params["playlistId"]]
                    return {"items": [{"contentDetails": {"videoId": video_id}}]}
                if resource == "videos":
                    return {"items": [videos[video_id] for video_id in params["id"].split(",")]}
                raise AssertionError(resource)

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                edition="public",
                retention_days=30,
                creator_retention_days=0,
            )
            service = TrackerService(config)
            service.database.upsert_channel(
                channel_item("UC-peer", "其他台V", "UU-peer"),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            service.database.upsert_channel(
                channel_item("UC-owned", "我的台V", "UU-owned"),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            service.database.set_settings({"owned_channel_id": "UC-owned"})
            fake_youtube = FakeYouTube()
            service.youtube = fake_youtube

            result = service.scan_hourly_live_candidates()

            playlist_calls = [call for call in fake_youtube.calls if call[0] == "playlistItems"]
            self.assertEqual(result["channels"], 2)
            self.assertFalse(result["quota_limited"])
            self.assertEqual(playlist_calls[0][1]["playlistId"], "UU-owned")
            self.assertEqual(playlist_calls[0][1]["maxResults"], 5)
            self.assertEqual(service.database.scalar(
                "SELECT live_state FROM videos WHERE video_id='video-owned-live'"
            ), "live")
            self.assertEqual(service.database.scalar(
                "SELECT live_state FROM videos WHERE video_id='video-peer-upcoming'"
            ), "upcoming")
            self.assertIsNone(service.database.scalar(
                "SELECT last_upload_scan_at FROM channels WHERE channel_id='UC-owned'"
            ))

            fake_youtube.calls.clear()
            fake_youtube.general_usage = 8500
            limited = service.scan_hourly_live_candidates()
            limited_playlists = [call for call in fake_youtube.calls if call[0] == "playlistItems"]
            self.assertEqual(limited["channels"], 1)
            self.assertTrue(limited["quota_limited"])
            self.assertEqual(limited_playlists[0][1]["playlistId"], "UU-owned")
            service.database.close()

    def test_live_poll_removes_missing_private_video_from_radar_and_can_restore_it(self):
        live_video = self.public_video_item("video-private-live", "UC-private-live")

        class FakeYouTube:
            def __init__(self):
                self.items = []

            def get(self, resource, params, bucket="general"):
                self.assert_resource(resource)
                return {"items": list(self.items)}

            @staticmethod
            def assert_resource(resource):
                if resource != "videos":
                    raise AssertionError(resource)

            @staticmethod
            def usage(bucket):
                return 0

            @staticmethod
            def safe_limit(bucket):
                return 9000

            @staticmethod
            def reset_at():
                return ""

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                api_key="",
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                edition="public",
                retention_days=30,
                creator_retention_days=0,
                discovery_pages_per_term=1,
                quota_general_limit=10_000,
                quota_search_limit=100,
                quota_safety_percent=10,
            )
            service = TrackerService(config)
            service.database.upsert_channel(
                self.public_channel_item("UC-private-live", "後來轉成私人影片的頻道"),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            service.database.upsert_video(live_video)
            fake_youtube = FakeYouTube()
            service.youtube = fake_youtube

            service.poll_live()

            unavailable = service.database.rows(
                "SELECT live_state,current_concurrent FROM videos WHERE video_id=?",
                ("video-private-live",),
            )[0]
            self.assertEqual(unavailable["live_state"], "unavailable")
            self.assertIsNone(unavailable["current_concurrent"])
            self.assertEqual(service.database.scalar(
                "SELECT COUNT(*) FROM concurrency_samples WHERE video_id=?",
                ("video-private-live",),
            ), 1)
            self.assertEqual(service.summary()["live_videos"], [])

            restored_video = json.loads(json.dumps(live_video))
            restored_video["liveStreamingDetails"]["concurrentViewers"] = "12"
            fake_youtube.items = [restored_video]
            service.refresh_videos(["video-private-live"])

            restored = service.database.rows(
                "SELECT live_state,current_concurrent FROM videos WHERE video_id=?",
                ("video-private-live",),
            )[0]
            self.assertEqual(restored["live_state"], "live")
            self.assertEqual(restored["current_concurrent"], 12)
            self.assertEqual(len(service.summary()["live_videos"]), 1)
            service.database.close()

    def test_missing_upload_playlist_skips_channel_and_preserves_batch(self):
        def channel_item(channel_id, title, uploads):
            return {
                "id": channel_id,
                "snippet": {"title": title, "description": "台V", "thumbnails": {}},
                "statistics": {"subscriberCount": "2000", "viewCount": "10000", "videoCount": "10"},
                "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
                "brandingSettings": {"channel": {}},
            }

        existing_video = {
            "id": "video-existing",
            "snippet": {"channelId": "UC-broken", "title": "既有影片", "thumbnails": {}},
            "statistics": {"viewCount": "100"},
            "contentDetails": {"duration": "PT10M"},
        }
        healthy_video = {
            "id": "video-healthy",
            "snippet": {"channelId": "UC-healthy", "title": "新影片", "thumbnails": {}},
            "statistics": {"viewCount": "25"},
            "contentDetails": {"duration": "PT12M"},
        }

        class FakeYouTube:
            def usage(self, bucket):
                return 0

            def safe_limit(self, bucket):
                return 9000

            def get(self, resource, params, bucket="general"):
                if resource == "playlistItems" and params["playlistId"] == "UU-broken":
                    raise YouTubeAPIError(
                        404,
                        "playlistNotFound",
                        '{"error":{"errors":[{"reason":"playlistNotFound"}]}}',
                    )
                if resource == "playlistItems":
                    return {"items": [{"contentDetails": {"videoId": "video-healthy"}}]}
                if resource == "videos":
                    return {"items": [healthy_video]}
                raise AssertionError(resource)

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                api_key="test-key",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                edition="public",
                retention_days=30,
                creator_retention_days=0,
            )
            service = TrackerService(config)
            service.database.upsert_channel(
                channel_item("UC-broken", "失效頻道", "UU-broken"),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            service.database.upsert_channel(
                channel_item("UC-healthy", "正常頻道", "UU-healthy"),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            service.database.upsert_video(existing_video)
            service.youtube = FakeYouTube()

            diagnostics = io.StringIO()
            with redirect_stderr(diagnostics):
                service.scan_due_uploads(limit=10)

            self.assertEqual(service.database.scalar(
                "SELECT title FROM videos WHERE video_id='video-existing'"
            ), "既有影片")
            self.assertEqual(service.database.scalar(
                "SELECT title FROM videos WHERE video_id='video-healthy'"
            ), "新影片")
            self.assertIsNotNone(service.database.scalar(
                "SELECT last_upload_scan_at FROM channels WHERE channel_id='UC-broken'"
            ))
            self.assertIn("已略過「失效頻道」", service.last_warning)
            self.assertIn("既有資料已保留", service.last_warning)
            self.assertNotIn("playlistNotFound", service.last_warning)
            self.assertIn("channel_id=UC-broken", diagnostics.getvalue())
            self.assertIn("playlist_id=UU-broken", diagnostics.getvalue())
            self.assertIn("playlistNotFound", diagnostics.getvalue())
            service.database.close()

    def test_channel_refresh_preserves_eligibility(self):
        item = {
            "id": "UC-test",
            "snippet": {"title": "測試台V", "description": "台V頻道", "thumbnails": {}},
            "statistics": {"subscriberCount": "1500", "viewCount": "20000", "videoCount": "10"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-test"}},
            "brandingSettings": {"channel": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_channel(item, status="eligible", evidence=("台V", "頻道名稱", "測試台V"))
            item["statistics"]["subscriberCount"] = "1600"
            database.upsert_channel(item)
            row = database.rows("SELECT discovery_status,subscriber_count FROM channels")[0]
            self.assertEqual(row["discovery_status"], "eligible")
            self.assertEqual(row["subscriber_count"], 1600)
            database.close()

    def test_manual_exclusion_deletes_channel_data_and_blacklists(self):
        item = {
            "id": "UC-test",
            "snippet": {"title": "誤收頻道", "description": "台V頻道", "thumbnails": {}},
            "statistics": {"subscriberCount": "1500", "viewCount": "20000", "videoCount": "10"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-test"}},
            "brandingSettings": {"channel": {}},
        }
        video = {
            "id": "video-test",
            "snippet": {"channelId": "UC-test", "title": "測試直播", "thumbnails": {}},
            "statistics": {"viewCount": "100"},
            "contentDetails": {"duration": "PT1H"},
            "liveStreamingDetails": {"actualStartTime": "2026-07-27T10:00:00Z", "concurrentViewers": "88"},
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.upsert_channel(item, status="eligible", evidence=("台V", "頻道名稱", "誤收頻道"))
            database.upsert_video(video)

            removed = database.exclude_channel("UC-test")

            self.assertEqual(removed["title"], "誤收頻道")
            self.assertTrue(database.is_excluded("UC-test"))
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM channels"), 0)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM channel_snapshots"), 0)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM videos"), 0)
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM concurrency_samples"), 0)

            database.restore_excluded("UC-test")
            self.assertFalse(database.is_excluded("UC-test"))
            database.close()

    def test_broad_discovery_does_not_restore_excluded_channel(self):
        item = {
            "id": "UC-test",
            "snippet": {"title": "誤收台V", "description": "台V頻道", "thumbnails": {}},
            "statistics": {"subscriberCount": "1500", "viewCount": "20000", "videoCount": "10"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-test"}},
            "brandingSettings": {"channel": {}},
        }

        class FakeYouTube:
            def get(self, resource, params, bucket="general"):
                if resource == "search":
                    return {"items": [{"id": {"channelId": "UC-test"}}]}
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                discovery_pages_per_term=1,
            )
            service = TrackerService(config)
            service.youtube = FakeYouTube()
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道名稱", "誤收台V"))
            service.database.exclude_channel("UC-test")

            service.discover_channels()

            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM channels"), 0)
            self.assertTrue(service.database.is_excluded("UC-test"))
            service.database.close()

    def test_specific_add_can_restore_an_excluded_channel(self):
        item = {
            "id": "UC-test",
            "snippet": {"title": "指定頻道", "description": "沒有自述字樣", "thumbnails": {}},
            "statistics": {"subscriberCount": "2500", "viewCount": "30000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-test"}},
            "brandingSettings": {"channel": {}},
        }

        class FakeYouTube:
            def get(self, resource, params, bucket="general"):
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(database_path=Path(directory) / "test.sqlite3", min_subscribers=1000)
            service = TrackerService(config)
            service.youtube = FakeYouTube()
            service.database.upsert_channel(item, status="eligible", evidence=("手動指定", "指定搜尋", "指定頻道"))
            service.database.exclude_channel("UC-test")

            service.add_manual_channel("UC-test")

            row = service.database.rows(
                "SELECT discovery_status,match_term FROM channels WHERE channel_id='UC-test'"
            )[0]
            self.assertEqual(row["discovery_status"], "eligible")
            self.assertEqual(row["match_term"], "手動指定")
            self.assertFalse(service.database.is_excluded("UC-test"))
            service.database.close()

    def test_settings_and_category_are_persisted(self):
        item = {
            "id": "UC-test",
            "snippet": {"title": "分類測試頻道", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3000", "viewCount": "40000", "videoCount": "30"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-test"}},
            "brandingSettings": {"channel": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.sqlite3"
            config = SimpleNamespace(
                database_path=database_path,
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=30,
            )
            service = TrackerService(config)
            saved = service.update_settings({
                "min_subscribers": 2000,
                "live_poll_seconds": 90,
                "discovery_terms": ["台V", "島嶼箱庭"],
            })
            self.assertEqual(saved["min_subscribers"], 2000)
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.update_channel_category("UC-test", "歌勢")
            service.database.close()

            reopened = TrackerService(config)
            self.assertEqual(reopened.settings_payload()["live_poll_seconds"], 90)
            self.assertEqual(reopened.settings_payload()["discovery_terms"], ["台V", "島嶼箱庭"])
            self.assertEqual(
                reopened.settings_payload()["enhanced_live_scan_times"],
                ["00:05", "01:05", "08:05", "12:05", "15:05", "18:05", "19:05", "20:05", "21:05", "22:05", "23:05"],
            )
            detail = reopened.channel_detail("UC-test")
            self.assertEqual(detail["channel"]["category"], "歌勢")
            self.assertGreaterEqual(len(detail["snapshots"]), 1)
            reopened.database.close()

    def test_content_insights_aggregate_same_scale_channels(self):
        stream_end = datetime.now(UTC).replace(microsecond=0)
        stream_start = stream_end - timedelta(minutes=90)
        item = {
            "id": "UC-insight",
            "snippet": {"title": "同級測試台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "4200", "viewCount": "80000", "videoCount": "40"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-insight"}},
            "brandingSettings": {"channel": {}},
        }
        video = {
            "id": "video-asmr",
            "snippet": {
                "channelId": "UC-insight",
                "title": "深夜助眠 ASMR #睡眠",
                "description": "安靜陪伴",
                "publishedAt": stream_start.isoformat(),
                "categoryId": "24",
                "tags": ["ASMR", "睡眠"],
                "thumbnails": {},
            },
            "statistics": {"viewCount": "2100", "likeCount": "180", "commentCount": "12"},
            "contentDetails": {"duration": "PT2H"},
            "liveStreamingDetails": {
                "actualStartTime": stream_start.isoformat(),
                "actualEndTime": stream_end.isoformat(),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                api_key="",
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                edition="public",
                retention_days=30,
                creator_retention_days=0,
                discovery_pages_per_term=1,
                quota_general_limit=10_000,
                quota_search_limit=100,
                quota_safety_percent=10,
            )
            service = TrackerService(config)
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.database.upsert_video(video)
            own_short = {
                "id": "video-own-short",
                "snippet": {
                    "channelId": "UC-insight",
                    "title": "我的上船迫遷 Shorts",
                    "description": "短片",
                    "publishedAt": stream_end.isoformat(),
                    "categoryId": "24",
                    "tags": ["Shorts"],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "2100", "likeCount": "120", "commentCount": "8"},
                "contentDetails": {"duration": "PT45S"},
            }
            service.database.upsert_video(own_short)
            peer_item = {
                "id": "UC-peer",
                "snippet": {"title": "同級夥伴台V", "description": "台V", "thumbnails": {}},
                "statistics": {"subscriberCount": "5000", "viewCount": "95000", "videoCount": "50"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-peer"}},
                "brandingSettings": {"channel": {}},
            }
            peer_video = {
                "id": "video-peer-asmr",
                "snippet": {
                    "channelId": "UC-peer",
                    "title": "耳邊陪伴 ASMR",
                    "description": "助眠",
                    "publishedAt": stream_start.isoformat(),
                    "categoryId": "24",
                    "tags": ["ASMR"],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "2500", "likeCount": "210", "commentCount": "15"},
                "contentDetails": {"duration": "PT90M"},
                "liveStreamingDetails": {"actualStartTime": stream_start.isoformat(), "actualEndTime": stream_end.isoformat()},
            }
            service.database.upsert_channel(peer_item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.database.upsert_video(peer_video)
            peer_short = {
                "id": "video-peer-short",
                "snippet": {
                    "channelId": "UC-peer",
                    "title": "同級 Shorts",
                    "description": "短片",
                    "publishedAt": stream_end.isoformat(),
                    "categoryId": "24",
                    "tags": ["Shorts"],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "2000", "likeCount": "90", "commentCount": "5"},
                "contentDetails": {"duration": "PT40S"},
            }
            service.database.upsert_video(peer_short)
            service.database.executemany(
                "INSERT INTO concurrency_samples(video_id,captured_at,concurrent_viewers) VALUES (?,?,?)",
                (
                    ("video-peer-asmr", (stream_start + timedelta(minutes=index)).isoformat(), 333)
                    for index in range(90)
                ),
            )

            result = service.insights(
                days=30,
                min_subscribers=2000,
                max_subscribers=8000,
                reference_channel_id="UC-insight",
            )

            self.assertEqual(result["overview"]["channels"], 1)
            self.assertEqual(result["overview"]["recent_streams"], 1)
            asmr_breakdown = next(
                row for row in result["content_breakdown"] if row["content_type"] == "ASMR"
            )
            self.assertEqual(result["reference"]["title"], "同級測試台V")
            self.assertEqual(result["top_videos"][0]["video_id"], "video-peer-asmr")
            self.assertEqual(result["top_videos_by_format"]["直播"][0]["video_id"], "video-peer-asmr")
            self.assertEqual(result["top_videos_by_format"]["直播"][0]["average_concurrent"], 333)
            self.assertEqual(result["reference_top_videos_by_format"]["Shorts"]["video_id"], "video-own-short")
            self.assertEqual(result["reference_top_videos_by_format"]["Shorts"]["peer_rank"], 1)
            self.assertEqual(result["reference_top_videos_by_format"]["Shorts"]["comparison_count"], 2)
            self.assertEqual(
                asmr_breakdown["representative_videos"][0]["channel_id"],
                "UC-peer",
            )
            self.assertEqual(result["content_landscapes"]["主要內容"]["items"], 1)
            self.assertEqual(result["content_landscapes"]["Shorts"]["items"], 1)
            self.assertEqual(result["schedule_summary"]["channel_count"], 1)
            self.assertEqual(result["schedule_summary"]["stream_count"], 1)
            self.assertIn("開台最集中", result["schedule_summary"]["conclusion"])
            schedule_cell = next(
                cell for row in result["schedule"] for cell in row if cell["streams"]
            )
            self.assertEqual(schedule_cell["median_peak"], 333)
            self.assertEqual(schedule_cell["streams"][0]["video_id"], "video-peer-asmr")
            self.assertEqual(schedule_cell["streams"][0]["channel_title"], "同級夥伴台V")
            self.assertEqual(
                result["classification_guide"]["representative_ranking"],
                "一般影片與 Shorts 依觀看／訂閱比排序；直播依完整取樣的平均同接／訂閱比排序，每個頻道最多一項。",
            )
            summary_channel = next(row for row in service.summary()["channels"] if row["channel_id"] == "UC-peer")
            self.assertEqual(summary_channel["median_average_concurrent"], 333)
            self.assertEqual(summary_channel["concurrency_covered_streams"], 1)
            self.assertGreater(summary_channel["weekly_streams"], 0)
            self.assertGreater(summary_channel["weekly_shorts"], 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM video_snapshots"), 4)
            service.database.close()

    def test_manual_content_classification_persists_and_teaches_game_name(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=30,
            )
            service = TrackerService(config)
            service.database.upsert_channel({
                "id": "UC-classification",
                "snippet": {"title": "分類測試台V", "description": "台V", "thumbnails": {}},
                "statistics": {"subscriberCount": "3500", "viewCount": "50000", "videoCount": "20"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-classification"}},
                "brandingSettings": {"channel": {}},
            }, status="eligible", evidence=("台V", "頻道說明", "台V"))
            morning_video = {
                "id": "video-morning",
                "snippet": {
                    "channelId": "UC-classification",
                    "title": "早安台，一起吃早餐",
                    "description": "",
                    "publishedAt": utc_now(),
                    "categoryId": "20",
                    "tags": ["singing", "歌回"],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "900"},
                "contentDetails": {"duration": "PT1H"},
                "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
            }
            service.database.upsert_video(morning_video)
            self.assertEqual(
                service.database.scalar("SELECT content_type FROM videos WHERE video_id='video-morning'"),
                "雜談",
            )

            corrected = service.update_video_classification({
                "video_id": "video-morning",
                "topics": ["遊戲"],
                "game_name": "自訂冒險",
                "note": "使用者確認實際遊玩內容",
            })
            self.assertEqual(corrected["classification_source"], "人工確認")
            service.database.upsert_video(morning_video)
            self.assertEqual(
                service.database.scalar("SELECT content_type FROM videos WHERE video_id='video-morning'"),
                "遊戲",
            )

            service.database.upsert_video({
                "id": "video-learned-game",
                "snippet": {
                    "channelId": "UC-classification",
                    "title": "自訂冒險 新手村開荒",
                    "description": "",
                    "publishedAt": utc_now(),
                    "categoryId": "24",
                    "tags": [],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "800"},
                "contentDetails": {"duration": "PT2H"},
                "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
            })
            learned = service.database.rows(
                "SELECT content_type,classification_source,game_name FROM videos WHERE video_id='video-learned-game'"
            )[0]
            self.assertEqual(learned["content_type"], "遊戲")
            self.assertEqual(learned["classification_source"], "標題（既有確認遊戲）")
            self.assertEqual(learned["game_name"], "自訂冒險")

            reset = service.reset_video_classification("video-morning")
            self.assertEqual(reset["content_type"], "雜談")
            self.assertNotEqual(reset["classification_source"], "人工確認")
            self.assertEqual(
                service.database.scalar("SELECT COUNT(*) FROM video_classification_overrides"), 0
            )
            service.database.close()

    def test_mixed_topics_are_displayed_together_and_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3", min_subscribers=1000,
                live_poll_seconds=60, channel_refresh_hours=6, upload_scan_hours=4,
                retention_days=30,
            ))
            service.database.upsert_channel({
                "id": "UC-mixed",
                "snippet": {"title": "混合主題台V", "description": "台V", "thumbnails": {}},
                "statistics": {"subscriberCount": "3000", "viewCount": "10000", "videoCount": "5"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-mixed"}},
                "brandingSettings": {"channel": {}},
            }, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.database.upsert_video({
                "id": "video-song-chat",
                "snippet": {
                    "channelId": "UC-mixed", "title": "深夜歌雜陪伴", "description": "",
                    "publishedAt": utc_now(), "categoryId": "24", "tags": [], "thumbnails": {},
                },
                "statistics": {"viewCount": "1200"},
                "contentDetails": {"duration": "PT2H"},
                "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
            })

            result = service.insights(days=30, min_subscribers=1000, max_subscribers=5000)
            landscape = result["content_landscapes"]["直播"]
            self.assertEqual(landscape["items"], 1)
            self.assertEqual(sum(row["items"] for row in landscape["content_breakdown"]), 1)
            self.assertEqual(landscape["content_breakdown"][0]["content_type"], "歌回 + 雜談")
            self.assertEqual(landscape["content_breakdown"][0]["share"], 100)
            service.database.close()

    def test_trends_exclude_reference_and_report_mature_deltas(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=90,
            )
            service = TrackerService(config)
            for channel_id, title, subscribers in (
                ("UC-own", "我的台V", 4200),
                ("UC-peer", "同級台V", 5000),
            ):
                service.database.upsert_channel({
                    "id": channel_id,
                    "snippet": {"title": title, "description": "台V", "thumbnails": {}},
                    "statistics": {"subscriberCount": str(subscribers), "viewCount": "100000", "videoCount": "50"},
                    "contentDetails": {"relatedPlaylists": {"uploads": f"UU-{channel_id}"}},
                    "brandingSettings": {"channel": {}},
                }, status="eligible", evidence=("台V", "頻道說明", "台V"))
                service.database.upsert_video({
                    "id": f"video-{channel_id}",
                    "snippet": {
                        "channelId": channel_id,
                        "title": "本月遊戲實況",
                        "description": "",
                        "publishedAt": utc_now(),
                        "categoryId": "20",
                        "tags": [],
                        "thumbnails": {},
                    },
                    "statistics": {"viewCount": "2000", "likeCount": "100", "commentCount": "10"},
                    "contentDetails": {"duration": "PT2H"},
                    "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
                })
            old_at = (datetime.now(UTC) - timedelta(days=31)).isoformat(timespec="seconds")
            service.database.execute(
                "INSERT INTO channel_snapshots(channel_id,captured_at,subscriber_count,view_count,video_count) VALUES (?,?,?,?,?)",
                ("UC-own", old_at, 4000, 90000, 48),
            )
            service.database.execute(
                "INSERT INTO channel_snapshots(channel_id,captured_at,subscriber_count,view_count,video_count) VALUES (?,?,?,?,?)",
                ("UC-peer", old_at, 4800, 90000, 48),
            )

            result = service.trends(
                days=30,
                min_subscribers=2000,
                max_subscribers=8000,
                reference_channel_id="UC-own",
            )

            self.assertEqual(result["overview"]["peer_channels"], 1)
            self.assertEqual(result["reference"]["channel_id"], "UC-own")
            self.assertTrue(result["reference"]["subscriber_delta_30"]["ready"])
            self.assertEqual(result["reference"]["subscriber_delta_30"]["change"], 200)
            self.assertTrue(result["readiness"]["month_ready"])
            self.assertEqual(result["series"][0]["channel_id"], "UC-own")
            self.assertIn("video_count", result["series"][0]["points"][0])
            self.assertIn("view_count", result["peer_series"][0])
            self.assertIn("video_count", result["peer_series"][0])
            self.assertEqual(result["comparison_channels"][0]["channel_id"], "UC-own")
            self.assertEqual(result["rankings"]["top_videos"][0]["format_type"], "直播")
            service.database.close()

    def test_trends_topic_filter_and_organization_scope_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=90,
            )
            service = TrackerService(config)
            for channel_id, title, subscribers, organization in (
                ("UC-reference", "基準台V", 4000, ""),
                ("UC-peer", "同級台V", 3000, "同級社"),
                ("UC-large", "大型台V", 100000, "大型社"),
            ):
                service.database.upsert_channel({
                    "id": channel_id,
                    "snippet": {"title": title, "description": "台V", "thumbnails": {}},
                    "statistics": {"subscriberCount": str(subscribers), "viewCount": "100000", "videoCount": "50"},
                    "contentDetails": {"relatedPlaylists": {"uploads": f"UU-{channel_id}"}},
                    "brandingSettings": {"channel": {}},
                }, status="eligible", evidence=("台V", "頻道說明", "台V"))
                if organization:
                    service.database.update_channel_metadata(channel_id, "未分類", organization, [])

            for video_id, channel_id, title, category_id, views in (
                ("video-peer-game", "UC-peer", "Minecraft 遊戲實況", "20", 3000),
                ("video-peer-song", "UC-peer", "深夜歌回", "24", 2000),
                ("video-large-song", "UC-large", "大型歌回", "24", 50000),
            ):
                service.database.upsert_video({
                    "id": video_id,
                    "snippet": {
                        "channelId": channel_id,
                        "title": title,
                        "description": "",
                        "publishedAt": utc_now(),
                        "categoryId": category_id,
                        "tags": [],
                        "thumbnails": {},
                    },
                    "statistics": {"viewCount": str(views)},
                    "contentDetails": {"duration": "PT2H"},
                    "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
                })

            result = service.trends(
                days=3,
                min_subscribers=2000,
                max_subscribers=8000,
                reference_channel_id="UC-reference",
                format_type="全部",
                content_topic="歌回",
                organization_scope="all",
            )

            self.assertEqual(result["filters"]["days"], 7)
            self.assertEqual(result["overview"]["peer_channels"], 1)
            self.assertEqual(
                [video["video_id"] for video in result["rankings"]["top_videos"]],
                ["video-peer-song"],
            )
            self.assertEqual(
                {row["organization_name"] for row in result["rankings"]["organizations"]},
                {"同級社", "大型社"},
            )
            self.assertEqual(result["organization_coverage"], {
                "scope_channels": 3,
                "named_channels": 2,
            })

            peer_scope = service.trends(
                min_subscribers=2000,
                max_subscribers=8000,
                reference_channel_id="UC-reference",
                organization_scope="peer",
            )
            self.assertEqual(
                [row["organization_name"] for row in peer_scope["rankings"]["organizations"]],
                ["同級社"],
            )
            self.assertEqual(peer_scope["organization_coverage"], {
                "scope_channels": 1,
                "named_channels": 1,
            })
            service.database.close()

    def test_automatic_graduation_review_and_manual_lock(self):
        item = {
            "id": "UC-graduated",
            "snippet": {"title": "測試台V", "description": "本頻道已停止活動，謝謝大家", "thumbnails": {}},
            "statistics": {"subscriberCount": "2500", "viewCount": "40000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-graduated"}},
            "brandingSettings": {"channel": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(database_path=Path(directory) / "test.sqlite3", min_subscribers=1000)
            service = TrackerService(config)
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.evaluate_activity_status("UC-graduated")
            detail = service.channel_detail("UC-graduated")["channel"]
            self.assertEqual(detail["activity_status"], "疑似已畢業")
            self.assertEqual(detail["activity_status_confidence"], "high")

            service.update_channel_metadata("UC-graduated", {
                "category": "個人勢",
                "organization_name": "",
                "manual_tags": [],
                "activity_status": "已確認畢業",
            })
            recent_video = {
                "id": "return-stream",
                "snippet": {"channelId": "UC-graduated", "title": "復出直播", "publishedAt": utc_now(), "thumbnails": {}},
                "statistics": {"viewCount": "100"},
                "contentDetails": {"duration": "PT1H"},
                "liveStreamingDetails": {"actualStartTime": utc_now()},
            }
            service.database.upsert_video(recent_video)
            service.evaluate_activity_status("UC-graduated")
            locked = service.channel_detail("UC-graduated")["channel"]
            self.assertEqual(locked["activity_status"], "已確認畢業")
            self.assertIn("復出", locked["activity_status_reason"])
            service.database.close()

    def test_owned_channel_can_bypass_public_threshold(self):
        item = {
            "id": "UC-small-owner",
            "snippet": {"title": "小型台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "320", "viewCount": "5000", "videoCount": "8"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-small-owner"}},
            "brandingSettings": {"channel": {}},
        }

        class FakeYouTube:
            def get(self, resource, params, bucket="general"):
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(database_path=Path(directory) / "test.sqlite3", min_subscribers=1000)
            service = TrackerService(config)
            service.youtube = FakeYouTube()
            service.add_owned_channel("UC-small-owner")
            self.assertEqual(service.owned_channel_id(), "UC-small-owner")
            self.assertEqual(
                service.database.scalar("SELECT discovery_status FROM channels WHERE channel_id='UC-small-owner'"),
                "owned",
            )
            self.assertEqual(
                service.database.scalar("SELECT COUNT(*) FROM channels WHERE discovery_status='eligible'"),
                0,
            )
            self.assertEqual(service.creator_dashboard()["channel"]["title"], "小型台V")
            self.assertEqual(service.workspace_channel_ids(), ["UC-small-owner"])
            service.database.close()

    def test_manual_channel_reuses_search_payload_and_batches_refresh(self):
        item = {
            "id": "UC-manual-batch",
            "snippet": {"title": "批次台V", "description": "台V", "customUrl": "@batch", "thumbnails": {}},
            "statistics": {"subscriberCount": "1800", "viewCount": "8000", "videoCount": "12"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-manual-batch"}},
            "brandingSettings": {"channel": {}},
        }

        class FakeYouTube:
            def __init__(self):
                self.calls = 0

            def get(self, resource, params, bucket="general"):
                self.calls += 1
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3", min_subscribers=1000
            ))
            fake = FakeYouTube()
            service.youtube = fake
            self.assertEqual(service.search_channels("@batch")[0]["channel_id"], "UC-manual-batch")
            result = service.add_manual_channel("UC-manual-batch")
            self.assertEqual(fake.calls, 1)
            self.assertEqual(result["manual_refresh_queue_count"], 1)
            refreshed = service.refresh_manual_queue()
            self.assertEqual(fake.calls, 2)
            self.assertEqual(refreshed["updated"], 1)
            self.assertEqual(refreshed["remaining"], 0)
            service.database.close()

    def test_creator_workspace_supports_multiple_channels_and_scoped_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3", min_subscribers=1000
            ))
            for channel_id, title, subscribers in (
                ("UC-team-one", "企業藝人一", 3200),
                ("UC-team-two", "企業藝人二", 4100),
            ):
                service.database.upsert_channel({
                    "id": channel_id,
                    "snippet": {"title": title, "description": "台V", "thumbnails": {}},
                    "statistics": {"subscriberCount": str(subscribers), "viewCount": "50000", "videoCount": "20"},
                    "contentDetails": {"relatedPlaylists": {"uploads": f"UU-{channel_id}"}},
                    "brandingSettings": {"channel": {}},
                }, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.set_owned_channel("UC-team-one")
            service.set_owned_channel("UC-team-two")
            self.assertEqual(service.workspace_channel_ids(), ["UC-team-one", "UC-team-two"])
            dashboard = service.creator_dashboard("UC-team-one")
            self.assertEqual(dashboard["channel"]["title"], "企業藝人一")
            self.assertEqual(len(dashboard["workspace_channels"]), 2)
            service.import_creator_data({
                "channel_id": "UC-team-one",
                "filename": "team-one.csv",
                "content": "Date,Views\n2026-07-01,123\n",
            })
            self.assertEqual(service.creator_dashboard("UC-team-one")["imported_overview"]["views"], 123)
            self.assertIsNone(service.creator_dashboard("UC-team-two")["imported_overview"]["views"])
            service.remove_workspace_channel("UC-team-one")
            self.assertEqual(service.workspace_channel_ids(), ["UC-team-two"])
            self.assertEqual(service.database.scalar(
                "SELECT COUNT(*) FROM creator_import_batches WHERE channel_id='UC-team-one'"
            ), 1)
            service.database.close()

    def test_studio_csv_import_deduplicates_and_tracks_conflicts(self):
        item = {
            "id": "UC-creator",
            "snippet": {"title": "我的台V頻道", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3200", "viewCount": "50000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-creator"}},
            "brandingSettings": {"channel": {}},
        }
        csv_text = (
            "Date,Video,Video title,Views,Watch time (hours),Impressions,Impressions click-through rate\n"
            "2026-07-01,abc123,測試影片,100,4.5,1200,5.5%\n"
            "2026-07-02,abc123,測試影片,200,8.0,2300,6.0%\n"
        )
        parsed = parse_studio_csv(csv_text, "table.csv")
        self.assertEqual(len(parsed["rows"]), 2)
        self.assertEqual(parsed["rows"][0]["metrics"]["views"], 100)

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=30,
            )
            service = TrackerService(config)
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.set_owned_channel("UC-creator")
            payload = {"filename": "table.csv", "content": csv_text}
            preview = service.preview_creator_import(payload)
            self.assertEqual(preview["row_count"], 2)

            first = service.import_creator_data(payload)
            self.assertEqual(first["inserted_count"], 2)
            duplicate = service.import_creator_data(payload)
            self.assertTrue(duplicate["already_imported"])
            dashboard = service.creator_dashboard()
            self.assertEqual(dashboard["imported_overview"]["views"], 300)

            conflicting = csv_text.replace("100,4.5", "111,4.5")
            conflict_batch = service.import_creator_data({"filename": "updated.csv", "content": conflicting})
            self.assertEqual(conflict_batch["conflict_count"], 1)
            self.assertEqual(service.creator_dashboard()["imported_overview"]["views"], 311)

            manual = service.add_creator_manual_metric({
                "metric_date": "2026-07-03",
                "metric_name": "views",
                "metric_value": "25",
                "note": "補登",
            })
            self.assertEqual(manual["metric_value"], 25)
            self.assertEqual(len(service.creator_dashboard()["manual_metrics"]), 1)
            service.delete_creator_manual_metric(manual["id"])
            self.assertEqual(len(service.creator_dashboard()["manual_metrics"]), 0)
            service.database.close()

    def test_traditional_chinese_content_report_uses_totals_and_current_headers(self):
        item = {
            "id": "UC-studio-current",
            "snippet": {"title": "工作區測試台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3200", "viewCount": "50000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-studio-current"}},
            "brandingSettings": {"channel": {}},
        }
        csv_text = (
            "內容,影片標題,影片發布時間,時間長度,互動觀看次數,平均觀看比例 (%),"
            "非重複觀眾人數,回訪的觀眾,獲得的訂閱人數,流失的訂閱人數,"
            "已新增留言,預估收益 (TWD),觀看次數\n"
            "總計,,,,300,7.44,80,30,15,2,12,99.5,500\n"
            'video-one,第一支影片,"May 9, 2026",120,200,8.5,50,20,10,1,8,60,300\n'
            'video-two,第二支影片,"May 2, 2026",90,100,6.3,35,12,5,1,4,39.5,200\n'
        )
        parsed = parse_studio_csv(csv_text, "content.csv")
        self.assertEqual(len(parsed["rows"]), 3)
        self.assertEqual(parsed["rows"][0]["row_kind"], "total")
        self.assertEqual(parsed["rows"][1]["published_at"], "2026-05-09")
        self.assertIn("engaged_views", parsed["recognized_metrics"])
        self.assertIn("estimated_revenue", parsed["recognized_metrics"])
        self.assertIn("published_at", parsed["recognized_dimensions"])

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=30,
            )
            service = TrackerService(config)
            service.database.upsert_channel(
                item, status="eligible", evidence=("台V", "頻道說明", "台V")
            )
            service.set_owned_channel("UC-studio-current")
            preview = service.preview_creator_import({
                "filename": "content.csv",
                "content": csv_text,
            })
            self.assertEqual(preview["recognized_column_count"], 13)
            service.import_creator_data({
                "filename": "content.csv",
                "content": csv_text,
            })
            dashboard = service.creator_dashboard()
            self.assertEqual(dashboard["imported_overview"]["views"], 500)
            self.assertEqual(dashboard["imported_overview"]["engaged_views"], 300)
            self.assertEqual(dashboard["imported_overview"]["estimated_revenue"], 99.5)
            service.database.close()

    def test_creator_analytics_sorts_filters_and_paginates_all_rows(self):
        item = {
            "id": "UC-analytics-browser",
            "snippet": {"title": "解析資料測試台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3200", "viewCount": "50000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-analytics-browser"}},
            "brandingSettings": {"channel": {}},
        }
        csv_lines = [
            "日期,內容,影片標題,時間長度,觀看次數,曝光次數",
            "總計,,,,4500,40000",
        ]
        for index in range(1, 31):
            title = f"WARFRAME 遊戲實況 #{index}" if index % 2 else f"晚安雜談 #{index}"
            duration = 30 if index == 30 else 120
            views = "" if index == 29 else str(index * 10)
            csv_lines.append(
                f"2026-07-{index:02d},video-{index},{title},{duration},{views},{1000 + index}"
            )
        csv_text = "\n".join(csv_lines) + "\n"

        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
            ))
            service.database.upsert_channel(
                item, status="eligible", evidence=("台V", "頻道說明", "台V")
            )
            service.set_owned_channel("UC-analytics-browser")
            service.import_creator_data({"filename": "content.csv", "content": csv_text})

            first_page = service.creator_analytics(
                sort="views", direction="desc", page=1, page_size=25
            )
            self.assertEqual(first_page["available_count"], 31)
            self.assertEqual(first_page["result_count"], 30)
            self.assertEqual(first_page["page_count"], 2)
            self.assertEqual(len(first_page["rows"]), 25)
            self.assertEqual(first_page["rows"][0]["views"], 300)

            second_page = service.creator_analytics(
                sort="views", direction="desc", page=2, page_size=25
            )
            self.assertEqual(len(second_page["rows"]), 5)
            self.assertIsNone(second_page["rows"][-1]["views"])

            game_rows = service.creator_analytics(
                query="WARFRAME", content_topic="遊戲", page_size=25
            )
            self.assertEqual(game_rows["result_count"], 15)
            self.assertTrue(all(row["content_topic"] == "遊戲" for row in game_rows["rows"]))
            self.assertTrue(all(
                row["classification_source"] == "匯入標題規則" for row in game_rows["rows"]
            ))

            date_rows = service.creator_analytics(
                date_start="2026-07-10",
                date_end="2026-07-12",
                sort="date",
                direction="asc",
                page_size=25,
            )
            self.assertEqual(
                [row["display_date"] for row in date_rows["rows"]],
                ["2026-07-10", "2026-07-11", "2026-07-12"],
            )
            self.assertTrue(all(row["date_source"] == "資料日期" for row in date_rows["rows"]))

            shorts = service.creator_analytics(content_format="Shorts", page_size=25)
            self.assertEqual(shorts["result_count"], 1)
            totals = service.creator_analytics(row_kind="total", page_size=25)
            self.assertEqual(totals["result_count"], 1)
            self.assertEqual(totals["rows"][0]["row_kind"], "total")
            with self.assertRaises(ValueError):
                service.creator_analytics(date_start="2026-07-31", date_end="2026-07-01")
            service.database.close()

    def test_retention_choices_separate_public_and_creator_data(self):
        item = {
            "id": "UC-retention",
            "snippet": {"title": "保存測試台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3200", "viewCount": "50000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-retention"}},
            "brandingSettings": {"channel": {}},
        }
        old_date = (datetime.now(UTC) - timedelta(days=45)).date().isoformat()
        old_time = f"{old_date}T00:00:00+00:00"
        csv_text = f"Date,Views\n{old_date},100\n"

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=1_095,
                creator_retention_days=30,
            )
            service = TrackerService(config)
            self.assertEqual(service.settings_payload()["retention_days"], 30)
            with self.assertRaises(ValueError):
                service.update_settings({"retention_days": 180})
            self.assertEqual(
                service.update_settings({"creator_retention_days": 365})["creator_retention_days"],
                365,
            )
            service.update_settings({"creator_retention_days": 30})

            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.set_owned_channel("UC-retention")
            service.import_creator_data({"filename": "old.csv", "content": csv_text})
            service.add_creator_manual_metric({
                "metric_date": old_date,
                "metric_name": "views",
                "metric_value": 25,
            })
            service.database.execute(
                """INSERT INTO channel_snapshots
                   (channel_id,captured_at,subscriber_count,view_count,video_count)
                   VALUES (?,?,?,?,?)""",
                ("UC-retention", old_time, 3000, 40000, 10),
            )

            service.cleanup()
            self.assertEqual(service.database.scalar(
                "SELECT COUNT(*) FROM channel_snapshots WHERE captured_at=?", (old_time,)
            ), 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM creator_analytics_rows"), 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM creator_manual_metrics"), 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM creator_import_batches"), 0)
            service.database.close()

    def test_personal_edition_can_keep_public_snapshots_indefinitely(self):
        item = {
            "id": "UC-personal-retention",
            "snippet": {"title": "私人保存測試台V", "description": "台V", "thumbnails": {}},
            "statistics": {"subscriberCount": "3200", "viewCount": "50000", "videoCount": "20"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-personal-retention"}},
            "brandingSettings": {"channel": {}},
        }
        old_time = (datetime.now(UTC) - timedelta(days=400)).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                edition="personal",
                min_subscribers=1000,
                live_poll_seconds=60,
                channel_refresh_hours=6,
                upload_scan_hours=4,
                retention_days=0,
                creator_retention_days=0,
            )
            service = TrackerService(config)
            self.assertEqual(service.settings_payload()["edition"], "personal")
            self.assertEqual(service.settings_payload()["retention_days"], 0)
            self.assertIn(0, service.settings_payload()["retention_options"])
            service.database.upsert_channel(item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.database.execute(
                """INSERT INTO channel_snapshots
                   (channel_id,captured_at,subscriber_count,view_count,video_count)
                   VALUES (?,?,?,?,?)""",
                ("UC-personal-retention", old_time, 3000, 40000, 10),
            )
            service.cleanup()
            self.assertEqual(service.database.scalar(
                "SELECT COUNT(*) FROM channel_snapshots WHERE captured_at=?", (old_time,)
            ), 1)
            service.database.close()

    def test_channel_metadata_and_discovery_progress(self):
        item = {
            "id": "UC-metadata",
            "snippet": {"title": "測試台V", "description": "台V頻道", "thumbnails": {}},
            "statistics": {"subscriberCount": "1800", "viewCount": "10000", "videoCount": "5"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-metadata"}},
            "brandingSettings": {"channel": {}},
        }

        class FakeYouTube:
            def get(self, resource, params, bucket="general"):
                if resource == "search":
                    return {"items": [{"id": {"channelId": "UC-metadata"}}]}
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                discovery_pages_per_term=1,
            )
            service = TrackerService(config)
            service.youtube = FakeYouTube()
            service.discover_channels()
            self.assertEqual(service.discovery_progress["status"], "completed")
            self.assertEqual(service.discovery_progress["new_count"], 1)
            saved = service.update_channel_metadata("UC-metadata", {
                "category": "企業勢",
                "organization_name": "子午計畫",
                "manual_tags": ["歌勢", "雙語"],
            })
            self.assertEqual(saved["organization_name"], "子午計畫")
            detail = service.channel_detail("UC-metadata")
            self.assertEqual(detail["channel"]["manual_tags"], ["歌勢", "雙語"])
            service.database.close()

    def test_discovery_candidates_are_persisted_and_reviewed_without_new_search(self):
        item = {
            "id": "UC-review-candidate",
            "snippet": {
                "title": "一般直播頻道",
                "description": "主要進行遊戲直播",
                "customUrl": "@review-candidate",
                "thumbnails": {},
            },
            "statistics": {"subscriberCount": "800", "viewCount": "9000", "videoCount": "12"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU-review-candidate"}},
            "brandingSettings": {"channel": {"keywords": "遊戲 直播"}},
        }

        class FakeYouTube:
            def __init__(self):
                self.calls = 0

            def get(self, resource, params, bucket="general"):
                self.calls += 1
                if resource == "search":
                    return {
                        "items": [{
                            "id": {"channelId": "UC-review-candidate"},
                            "snippet": {
                                "title": "一般直播頻道",
                                "description": "主要進行遊戲直播",
                                "thumbnails": {},
                            },
                        }]
                    }
                return {"items": [item]}

        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                discovery_pages_per_term=1,
            )
            service = TrackerService(config)
            youtube = FakeYouTube()
            service.youtube = youtube
            service.discover_channels()
            calls_after_discovery = youtube.calls

            review = service.candidate_review()
            self.assertEqual(youtube.calls, calls_after_discovery)
            self.assertEqual(len(review["batches"]), 1)
            self.assertEqual(review["batches"][0]["status"], "completed")
            self.assertEqual(len(review["candidates"]), 1)
            candidate = review["candidates"][0]
            self.assertEqual(candidate["validation_status"], "rejected")
            self.assertEqual(candidate["current_status"], "pending")
            self.assertEqual(len(candidate["search_terms"]), 4)
            self.assertEqual(
                len(service.candidate_review(handling_status="pending")["candidates"]),
                1,
            )

            approved = service.review_candidate(candidate["id"], "approve")
            self.assertEqual(approved["channel_id"], "UC-review-candidate")
            self.assertEqual(service.database.scalar(
                "SELECT discovery_status FROM channels WHERE channel_id='UC-review-candidate'"
            ), "eligible")
            self.assertEqual(service.candidate_review()["candidates"][0]["current_status"], "included")
            service.review_candidate(candidate["id"], "exclude")
            self.assertEqual(service.candidate_review()["candidates"][0]["current_status"], "excluded")
            self.assertEqual(youtube.calls, calls_after_discovery)
            service.database.close()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_oauth_desktop_json_is_encrypted_and_authorization_uses_pkce(self):
        with tempfile.TemporaryDirectory() as directory:
            oauth = GoogleOAuth(
                Path(directory),
                "http://127.0.0.1:8787/api/creator/oauth/callback",
            )
            oauth.configure_client(b'{"installed":{"client_id":"test.apps.googleusercontent.com","client_secret":"not-a-real-secret"}}')
            stored = (Path(directory) / "youtube-oauth-client.dat").read_bytes()
            self.assertNotIn(b"test.apps.googleusercontent.com", stored)
            self.assertNotIn(b"not-a-real-secret", stored)
            url = oauth.authorization_url()
            self.assertIn("code_challenge_method=S256", url)
            self.assertIn("youtube.readonly", url)
            self.assertIn("yt-analytics.readonly", url)
            self.assertNotIn("not-a-real-secret", url)

    def test_untrusted_browser_origin_is_rejected(self):
        handler = RequestHandler.__new__(RequestHandler)
        handler.headers = Message()
        handler.headers["Origin"] = "https://example.invalid"
        self.assertFalse(handler._origin_allowed())
        handler.headers.replace_header("Origin", "http://127.0.0.1:3000")
        self.assertTrue(handler._origin_allowed())

    def test_oauth_analytics_sync_stays_in_separate_tables(self):
        class FakeOAuth:
            def status(self):
                return {"configured": True, "authorized": True, "scopes": []}

            def analytics_report(self, start_date, end_date, metrics, **options):
                dimension = options.get("dimensions", "")
                metric_names = metrics.split(",")
                headers = ([{"name": dimension}] if dimension else []) + [
                    {"name": name} for name in metric_names
                ]
                keys = ["2026-07-27", "2026-07-28"] if dimension == "day" else ["video-1"] if dimension == "video" else [None]
                values = {
                    "views": 120,
                    "estimatedMinutesWatched": 600,
                    "averageViewDuration": 30,
                    "averageViewPercentage": 42,
                    "subscribersGained": 8,
                    "subscribersLost": 3,
                    "engagedViews": 90,
                    "likes": 12,
                    "comments": 4,
                    "shares": 2,
                    "uniques": 70,
                }
                return {
                    "columnHeaders": headers,
                    "rows": [
                        ([key] if dimension else []) + [values[name] for name in metric_names]
                        for key in keys
                    ],
                }

            def video_details(self, video_ids):
                self.requested_video_ids = list(video_ids)
                return [{
                    "id": "video-1",
                    "snippet": {
                        "title": "OAuth 期間熱門影片",
                        "publishedAt": "2026-07-20T04:00:00Z",
                        "thumbnails": {"medium": {"url": "https://example.invalid/video-1.jpg"}},
                    },
                    "liveStreamingDetails": {"actualStartTime": "2026-07-21T12:30:00Z"},
                }]

        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                creator_retention_days=0,
            ))
            service.oauth = FakeOAuth()
            service.database.upsert_channel({
                "id": "UC-oauth",
                "snippet": {"title": "OAuth 頻道", "thumbnails": {}},
                "statistics": {"subscriberCount": "100", "viewCount": "1000", "videoCount": "10"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-oauth"}},
            }, status="owned")
            service.database.add_workspace_channel("UC-oauth")
            service.database.execute(
                "INSERT INTO creator_oauth_connections(channel_id,connected_at,last_error) VALUES (?,?, '')",
                ("UC-oauth", utc_now()),
            )
            result = service.sync_creator_oauth()
            self.assertEqual(result["daily_rows"], 2)
            self.assertEqual(result["video_rows"], 1)
            self.assertEqual(result["video_metadata_rows"], 1)
            payload = service.creator_oauth_data("UC-oauth")
            self.assertEqual(payload["summary"]["metrics"]["watch_time_hours"], 10)
            self.assertEqual(payload["summary"]["metrics"]["subscribers_net"], 5)
            self.assertEqual(len(payload["daily"]), 2)
            self.assertEqual(payload["videos"][0]["title"], "OAuth 期間熱門影片")
            self.assertEqual(payload["videos"][0]["thumbnail_url"], "https://example.invalid/video-1.jpg")
            self.assertEqual(payload["videos"][0]["published_at"], "2026-07-20T04:00:00Z")
            self.assertEqual(payload["videos"][0]["live_at"], "2026-07-21T12:30:00Z")
            self.assertEqual(payload["videos"][0]["content_date"], "2026-07-21T12:30:00Z")
            self.assertEqual(service.oauth.requested_video_ids, ["video-1"])
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM videos"), 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM creator_analytics_rows"), 0)
            service.database.close()

    def test_oauth_errors_have_actionable_traditional_chinese_guidance(self):
        disabled = oauth_error_guidance(
            "YouTube Analytics API has not been used in project 123 before or it is disabled."
        )
        self.assertEqual(disabled["code"], "analytics_api_disabled")
        self.assertIn("啟用", disabled["title"])
        self.assertIn("1～5 分鐘", " ".join(disabled["steps"]))
        self.assertNotIn("123", disabled["message"])

        denied = oauth_error_guidance("Error 403: access_denied")
        self.assertEqual(denied["code"], "oauth_test_user_missing")
        self.assertIn("測試使用者", " ".join(denied["steps"]))

        expired = oauth_error_guidance("invalid_grant: Token has been expired or revoked")
        self.assertEqual(expired["code"], "oauth_expired")
        self.assertIn("7 天", expired["message"])

    def test_oauth_sync_job_does_not_require_public_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TrackerService(SimpleNamespace(
                database_path=Path(directory) / "test.sqlite3",
                min_subscribers=1000,
                api_key="",
            ))
            completed = threading.Event()
            launched, message = service.launch_job("oauth-analytics-sync", completed.set)
            self.assertTrue(launched, message)
            self.assertTrue(completed.wait(2))
            service.database.close()

    def test_public_monitoring_package_uses_strict_public_whitelist(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "source.sqlite3")
            database.upsert_channel(
                self.public_channel_item(),
                status="eligible",
                evidence=("台V", "頻道說明", "台V 公開頻道說明"),
            )
            database.upsert_video(self.public_video_item())
            database.execute(
                "UPDATE videos SET tags=NULL WHERE video_id='video-public'"
            )
            database.execute(
                """INSERT INTO video_classification_overrides
                   (video_id,content_topics,game_name,note,updated_at) VALUES (?,?,?,?,?)""",
                ("video-public", '["遊戲"]', "測試遊戲", "使用者已確認", utc_now()),
            )
            database.execute(
                """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
                   VALUES (?,?,?,?)""",
                ("UC-blocked", "排除頻道", "manual", utc_now()),
            )
            database.add_workspace_channel("UC-public")
            database.upsert_creator_manual_metric(
                "UC-public", "2026-07-29", "", "views", 999, "PRIVATE-SENTINEL"
            )
            database.execute(
                """INSERT INTO creator_oauth_connections
                   (channel_id,connected_at,last_sync_at,last_data_date,last_error)
                   VALUES (?,?,?,?,?)""",
                ("UC-public", utc_now(), utc_now(), "2026-07-29", "PRIVATE-OAUTH-SENTINEL"),
            )
            database.execute(
                """INSERT INTO creator_oauth_summary_metrics
                   (channel_id,date_start,date_end,metrics_json,synced_at)
                   VALUES (?,?,?,?,?)""",
                (
                    "UC-public", "2026-07-01", "2026-07-29",
                    '{"private_metric":"PRIVATE-ANALYTICS-SENTINEL"}', utc_now(),
                ),
            )
            database.execute(
                """INSERT INTO creator_import_batches
                   (channel_id,filename,file_hash,imported_at) VALUES (?,?,?,?)""",
                ("UC-public", "PRIVATE-STUDIO-SENTINEL.csv", "test-private-hash", utc_now()),
            )
            database.set_settings({
                "owned_channel_id": "UC-public",
                "private_test_setting": "SECRET-SETTING-SENTINEL",
            })

            _, package_bytes, _ = database.export_public_monitoring()
            with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "manifest.json",
                        "data/channels.jsonl",
                        "data/channel_snapshots.jsonl",
                        "data/videos.jsonl",
                        "data/video_snapshots.jsonl",
                        "data/concurrency_samples.jsonl",
                        "data/video_classification_overrides.jsonl",
                    },
                )
                unpacked = b"\n".join(archive.read(name) for name in archive.namelist())
                channel_row = json.loads(archive.read("data/channels.jsonl").decode("utf-8"))
            self.assertNotIn(b"PRIVATE-SENTINEL", unpacked)
            self.assertNotIn(b"PRIVATE-OAUTH-SENTINEL", unpacked)
            self.assertNotIn(b"PRIVATE-ANALYTICS-SENTINEL", unpacked)
            self.assertNotIn(b"PRIVATE-STUDIO-SENTINEL", unpacked)
            self.assertNotIn(b"SECRET-SETTING-SENTINEL", unpacked)
            self.assertNotIn("match_term", channel_row)
            preview = database.preview_public_monitoring(package_bytes)
            self.assertTrue(preview["valid"])
            self.assertEqual(preview["format_version"], 1)
            self.assertEqual(preview["counts"]["channels"], 1)
            self.assertEqual(preview["counts"]["videos"], 1)
            self.assertEqual(preview["counts"]["video_classification_overrides"], 1)
            self.assertEqual(preview["counts"]["excluded_channels"], 0)

            destination = Database(Path(directory) / "nullable-tags-destination.sqlite3")
            destination.import_public_monitoring(package_bytes, "merge")
            self.assertIsNone(
                destination.scalar(
                    "SELECT tags FROM videos WHERE video_id='video-public'"
                )
            )
            destination.close()

            _, full_package, _ = database.export_public_monitoring(
                include_blacklist=True,
                include_source_evidence=True,
            )
            with zipfile.ZipFile(io.BytesIO(full_package)) as archive:
                self.assertIn("data/excluded_channels.jsonl", archive.namelist())
                full_channel = json.loads(archive.read("data/channels.jsonl").decode("utf-8"))
            self.assertEqual(full_channel["match_term"], "台V")
            self.assertEqual(
                database.preview_public_monitoring(full_package)["counts"]["excluded_channels"], 1
            )
            database.close()

    def test_public_monitoring_replace_preserves_private_sources_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Database(Path(directory) / "source.sqlite3")
            source.upsert_channel(
                self.public_channel_item("UC-imported", "搬入台V"),
                status="eligible",
                evidence=("台V", "頻道名稱", "搬入台V"),
            )
            source.upsert_video(self.public_video_item("video-imported", "UC-imported"))
            _, package_bytes, _ = source.export_public_monitoring(
                include_source_evidence=True
            )

            destination = Database(Path(directory) / "destination.sqlite3")
            destination.upsert_channel(
                self.public_channel_item("UC-private", "私人工作區台V"),
                status="eligible",
                evidence=("台V", "頻道名稱", "私人工作區台V"),
            )
            destination.upsert_video(self.public_video_item("video-old", "UC-private"))
            destination.add_workspace_channel("UC-private")
            destination.set_settings({"owned_channel_id": "UC-private"})
            destination.upsert_creator_manual_metric(
                "UC-private", "2026-07-28", "", "views", 321, "私人手動補值"
            )
            destination.execute(
                """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
                   VALUES (?,?,?,?)""",
                ("UC-local-blocked", "本機黑名單", "manual", utc_now()),
            )
            destination.execute(
                """INSERT INTO excluded_channels(channel_id,title,reason,excluded_at)
                   VALUES (?,?,?,?)""",
                ("UC-imported", "曾排除的搬入頻道", "manual", utc_now()),
            )

            result = destination.import_public_monitoring(package_bytes, "replace")

            self.assertEqual(result["preserved_private_channels"], 1)
            self.assertEqual(
                destination.scalar(
                    "SELECT discovery_status FROM channels WHERE channel_id='UC-private'"
                ),
                "owned",
            )
            self.assertEqual(
                destination.scalar(
                    "SELECT discovery_status FROM channels WHERE channel_id='UC-imported'"
                ),
                "eligible",
            )
            self.assertEqual(destination.scalar("SELECT COUNT(*) FROM creator_workspace_channels"), 1)
            self.assertEqual(destination.scalar("SELECT COUNT(*) FROM creator_manual_metrics"), 1)
            self.assertEqual(destination.get_setting("owned_channel_id", ""), "UC-private")
            self.assertEqual(destination.scalar("SELECT COUNT(*) FROM videos"), 1)
            self.assertEqual(
                destination.scalar("SELECT video_id FROM videos"), "video-imported"
            )
            self.assertEqual(destination.scalar("SELECT COUNT(*) FROM excluded_channels"), 1)
            self.assertFalse(destination.is_excluded("UC-imported"))

            before = {
                "channel_snapshots": destination.scalar("SELECT COUNT(*) FROM channel_snapshots"),
                "video_snapshots": destination.scalar("SELECT COUNT(*) FROM video_snapshots"),
                "concurrency_samples": destination.scalar("SELECT COUNT(*) FROM concurrency_samples"),
            }
            destination.import_public_monitoring(package_bytes, "merge")
            after = {
                "channel_snapshots": destination.scalar("SELECT COUNT(*) FROM channel_snapshots"),
                "video_snapshots": destination.scalar("SELECT COUNT(*) FROM video_snapshots"),
                "concurrency_samples": destination.scalar("SELECT COUNT(*) FROM concurrency_samples"),
            }
            self.assertEqual(after, before)
            source.close()
            destination.close()

    def test_public_monitoring_preview_rejects_modified_data_file(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "source.sqlite3")
            database.upsert_channel(
                self.public_channel_item(),
                status="eligible",
                evidence=("台V", "頻道說明", "台V"),
            )
            _, package_bytes, _ = database.export_public_monitoring()
            source_zip = zipfile.ZipFile(io.BytesIO(package_bytes))
            damaged_buffer = io.BytesIO()
            with source_zip, zipfile.ZipFile(damaged_buffer, "w", zipfile.ZIP_DEFLATED) as damaged:
                for name in source_zip.namelist():
                    content = source_zip.read(name)
                    if name == "data/channels.jsonl":
                        content = content.replace("公開台V".encode(), "偽造台V".encode())
                    damaged.writestr(name, content)
            database.close()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                database.preview_public_monitoring(damaged_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
