import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai_analysis import (
    OpenAISignalAnalyzer,
    compose_enriched_caption,
    extract_citation_urls,
    extract_output_text,
)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "AI: WAIT, уверенность 5/10\nФон: новостей нет\nПлан: ждать",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/coin-news",
                                        "title": "Coin news",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ).encode("utf-8")


def make_signal():
    return SimpleNamespace(
        symbol="AAAUSDT",
        source="BINANCE+BYBIT",
        mode="SHORT_TREND",
        signal_score=8,
        price=100,
        high_price=120,
        price_growth_lookback_pct=30,
        drawdown_from_high_pct=-16.67,
        price_change_window_pct=-2,
        oi_change_pct=1,
        cvd_delta_usdt=-50_000,
        funding_rate=0.0001,
    )


class OpenAIAnalysisTests(unittest.TestCase):
    def test_responses_request_uses_web_search_and_chart_image(self):
        analyzer = OpenAISignalAnalyzer("secret", model="gpt-5.6")
        with patch("openai_analysis.urlopen", return_value=FakeResponse()) as mocked:
            result = analyzer.analyze(make_signal(), b"png-bytes")

        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        content = payload["input"][0]["content"]
        image = next(item for item in content if item["type"] == "input_image")
        self.assertEqual(
            image["image_url"],
            "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("ascii"),
        )
        self.assertIn("AI: WAIT", result)
        self.assertIn("Источник: https://example.com/coin-news", result)

    def test_extract_output_text_supports_direct_property(self):
        self.assertEqual(extract_output_text({"output_text": "ready"}), "ready")

    def test_extract_citation_urls_uses_annotation_metadata(self):
        response = json.loads(FakeResponse().read().decode("utf-8"))
        self.assertEqual(extract_citation_urls(response), ["https://example.com/coin-news"])

    def test_photo_caption_never_exceeds_telegram_limit(self):
        caption = compose_enriched_caption("x" * 900, "y" * 500)
        self.assertEqual(len(caption), 1024)
        self.assertTrue(caption.endswith("…"))

    def test_photo_caption_preserves_clickable_source(self):
        source = "https://example.com/coin-news"
        analysis = f"{'y' * 500}\nИсточник: {source}"
        caption = compose_enriched_caption("x" * 900, analysis)
        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(caption.endswith(f"Источник: {source}"))


if __name__ == "__main__":
    unittest.main()
