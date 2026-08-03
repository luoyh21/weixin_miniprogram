import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from backend import topic_intel, topic_requests


class TopicRequestStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "requests.json"
        self.patch = mock.patch.object(topic_requests, "REQUESTS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_high_cost_request_waits_for_review(self):
        row = topic_requests.create(
            applicant="admin",
            title="太空具身智能",
            intro="",
            keywords=["空间机器人"],
            estimate={"tier": "high", "reason": "资料不足"},
        )
        self.assertEqual("pending", row["status"])
        self.assertEqual([row["id"]], [item["id"] for item in topic_requests.list_for("ADMIN")])

    def test_low_cost_request_is_queued_and_duplicate_is_rejected(self):
        row = topic_requests.create(
            applicant="admin",
            title="低成本专题",
            intro="",
            keywords=["专题"],
            estimate={"tier": "low", "reason": "资料充足"},
        )
        self.assertEqual("queued", row["status"])
        with self.assertRaisesRegex(ValueError, "已有申请"):
            topic_requests.create(
                applicant="admin2",
                title="低成本专题",
                intro="",
                keywords=["专题"],
                estimate={"tier": "low"},
            )

    def test_interrupted_work_becomes_retryable(self):
        row = topic_requests.create(
            applicant="admin",
            title="中断专题",
            intro="",
            keywords=["专题"],
            estimate={"tier": "low"},
        )
        topic_requests.update(row["id"], status="running")
        self.assertEqual(1, topic_requests.recover_interrupted())
        recovered = topic_requests.get(row["id"])
        self.assertEqual("failed", recovered["status"])
        self.assertIn("重启", recovered["error"])


class TopicEstimateTest(unittest.TestCase):
    def test_complete_local_material_is_low_cost(self):
        items = [{"body": "完整正文" * 100} for _ in range(12)]
        with mock.patch.object(topic_intel, "_request_candidates", return_value=items):
            estimate = topic_intel.estimate_request(["关键词"])
        self.assertEqual("low", estimate["tier"])
        self.assertEqual(0, estimate["estimated_llm_calls"])

    def test_seed_fetch_or_too_few_items_is_high_cost(self):
        items = [{"body": "短"}]
        with mock.patch.object(topic_intel, "_request_candidates", return_value=items):
            estimate = topic_intel.estimate_request(["关键词"], ["https://example.com/a"])
        self.assertEqual("high", estimate["tier"])
        self.assertEqual(1, estimate["seed_url_count"])

    def test_failed_seed_is_omitted_from_generated_topic(self):
        good = {
            "id": "good", "title": "可靠来源", "url": "https://good.example/a",
            "source": "good.example", "published": "", "region": "国外",
            "aspect": "专业技术", "summary": "", "tags": [],
        }

        def enrich(items):
            items[0]["body_zh"] = "完整正文" * 100

        request = {
            "id": "req1", "title": "测试专题", "intro": "",
            "keywords": ["测试"], "seed_urls": ["https://bad.example/a", "https://good.example/a"],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(topic_intel, "_request_candidates", return_value=[]), \
                mock.patch.object(topic_intel, "_seed_item", side_effect=[RuntimeError("fetch failed"), good]), \
                mock.patch.object(topic_intel, "_enrich_items", side_effect=enrich), \
                mock.patch.object(topic_intel, "_generate_pages"), \
                mock.patch.object(topic_intel, "_topic_path", return_value=Path(tmp) / "topic.json"):
            topic = topic_intel.build_requested_topic(request)
        self.assertEqual(1, topic["stats"]["count"])
        self.assertEqual("可靠来源", topic["items"][0]["title"])


class TopicApprovalNoticeTest(unittest.TestCase):
    def test_notice_contains_request_and_review_instructions(self):
        send_text = mock.Mock(return_value=[{"errcode": 0}])
        fake_src = types.ModuleType("src")
        fake_src.wecom = types.SimpleNamespace(send_text=send_text)
        request = {
            "title": "太空具身智能",
            "applicant": "admin",
            "estimate": {"reason": "资料不足", "estimated_fetches": 11},
        }
        with mock.patch.dict("sys.modules", {"src": fake_src}):
            topic_intel.notify_super_admin_request(request)
        message = send_text.call_args.args[0]
        self.assertIn("太空具身智能", message)
        self.assertIn("待审批", message)
        self.assertIn("11", message)
        self.assertEqual(topic_intel.ADMIN_PUSH_USER, send_text.call_args.kwargs["to_user"])


if __name__ == "__main__":
    unittest.main()
