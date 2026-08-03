import unittest

from backend.news_store import _content_completeness, _norm_intl


class ContentCompletenessTest(unittest.TestCase):
    def test_feed_ellipsis_is_incomplete(self):
        complete, notice = _content_completeness(
            "中文短摘要",
            "In March 2021, I hosted a discussion […] The post X appeared first on SpaceNews.",
        )
        self.assertFalse(complete)
        self.assertIn("并非完整正文", notice)

    def test_long_article_is_complete(self):
        complete, notice = _content_completeness("这是一段完整正文。" * 80)
        self.assertTrue(complete)
        self.assertEqual("", notice)

    def test_intl_detail_exposes_notice_and_images(self):
        item = _norm_intl({
            "title": "Short source",
            "title_zh": "短来源",
            "body_zh": "只有摘要……",
            "body_en": "A short excerpt [...]",
            "link": "https://example.com/a",
            "images": ["https://example.com/a.jpg"],
        })
        self.assertFalse(item["content_complete"])
        self.assertTrue(item["content_notice"])
        self.assertEqual(1, len(item["images"]))


if __name__ == "__main__":
    unittest.main()
