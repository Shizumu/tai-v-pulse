import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from collector.server import (
    Database,
    TrackerService,
    channel_query_target,
    classify_content_fields,
    evidence_for,
    evidence_for_terms,
    parse_duration,
    utc_now,
    video_format,
)


class CollectorTests(unittest.TestCase):
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
        self.assertEqual(video_format("video", 42, "短片"), "Shorts")
        self.assertEqual(video_format("completed", 7200, "直播存檔"), "直播")

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

    def test_database_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            self.assertEqual(database.scalar("SELECT COUNT(*) FROM channels"), 0)
            columns = {row["name"] for row in database.rows("PRAGMA table_info(channels)")}
            self.assertIn("category", columns)
            database.close()

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
            detail = reopened.channel_detail("UC-test")
            self.assertEqual(detail["channel"]["category"], "歌勢")
            self.assertGreaterEqual(len(detail["snapshots"]), 1)
            reopened.database.close()

    def test_content_insights_aggregate_same_scale_channels(self):
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
                "publishedAt": utc_now(),
                "categoryId": "24",
                "tags": ["ASMR", "睡眠"],
                "thumbnails": {},
            },
            "statistics": {"viewCount": "2100", "likeCount": "180", "commentCount": "12"},
            "contentDetails": {"duration": "PT2H"},
            "liveStreamingDetails": {
                "actualStartTime": utc_now(),
                "actualEndTime": utc_now(),
            },
        }
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
            service.database.upsert_video(video)

            result = service.insights(
                days=30,
                min_subscribers=2000,
                max_subscribers=8000,
                reference_channel_id="UC-insight",
            )

            self.assertEqual(result["overview"]["channels"], 1)
            self.assertEqual(result["overview"]["recent_streams"], 1)
            self.assertEqual(result["content_breakdown"][0]["content_type"], "ASMR")
            self.assertEqual(result["reference"]["title"], "同級測試台V")
            self.assertEqual(result["top_videos"][0]["video_id"], "video-asmr")
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM video_snapshots"), 1)
            service.database.close()


if __name__ == "__main__":
    unittest.main()
