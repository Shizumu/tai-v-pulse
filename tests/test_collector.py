import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from collector.server import (
    Database,
    TrackerService,
    channel_query_target,
    classify_content_fields,
    classify_content_labels,
    evidence_for,
    evidence_for_terms,
    load_env,
    parse_duration,
    parse_studio_csv,
    utc_now,
    video_format,
)


class CollectorTests(unittest.TestCase):
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
                    "publishedAt": utc_now(),
                    "categoryId": "24",
                    "tags": ["ASMR"],
                    "thumbnails": {},
                },
                "statistics": {"viewCount": "2500", "likeCount": "210", "commentCount": "15"},
                "contentDetails": {"duration": "PT90M"},
                "liveStreamingDetails": {"actualStartTime": utc_now(), "actualEndTime": utc_now()},
            }
            service.database.upsert_channel(peer_item, status="eligible", evidence=("台V", "頻道說明", "台V"))
            service.database.upsert_video(peer_video)

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
            self.assertEqual(result["top_videos"][0]["video_id"], "video-peer-asmr")
            self.assertEqual(result["top_videos_by_format"]["直播"][0]["video_id"], "video-peer-asmr")
            self.assertEqual(
                result["content_breakdown"][0]["representative_videos"][0]["channel_id"],
                "UC-peer",
            )
            self.assertEqual(result["content_landscapes"]["主要內容"]["items"], 1)
            self.assertEqual(result["content_landscapes"]["Shorts"]["items"], 0)
            self.assertEqual(service.database.scalar("SELECT COUNT(*) FROM video_snapshots"), 2)
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


if __name__ == "__main__":
    unittest.main()
