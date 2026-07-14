from __future__ import annotations

import base64
import json
import re
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TELEGRAM_PHOTO_CAPTION_LIMIT = 1024


class OpenAISignalAnalyzer:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-5.6",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 45,
        verify_ssl: bool = True,
        enabled: bool = True,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-5.6"
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(5, timeout_seconds)
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()
        self.configured_enabled = enabled

    @property
    def enabled(self) -> bool:
        return self.configured_enabled and bool(self.api_key)

    def analyze(self, signal, chart: bytes | None = None) -> str:
        if not self.enabled:
            return ""

        content: list[dict[str, str]] = [
            {"type": "input_text", "text": self._build_prompt(signal)}
        ]
        if chart:
            encoded_chart = base64.b64encode(chart).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded_chart}",
                }
            )

        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [{"type": "web_search", "search_context_size": "low"}],
            "tool_choice": "auto",
            "input": [{"role": "user", "content": content}],
        }
        result = self._create_response(payload)

        text = extract_output_text(result)
        if not text:
            raise RuntimeError("OpenAI returned no text analysis")
        analysis = normalize_analysis(text)
        citations = extract_citation_urls(result)
        if citations:
            analysis = f"{analysis}\nИсточник: {citations[0]}"
        return analysis

    def test_connection(self) -> tuple[str, bool]:
        if not self.configured_enabled:
            raise RuntimeError("OPENAI_ANALYSIS_ENABLED=false")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")

        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [{"type": "web_search", "search_context_size": "low"}],
            "tool_choice": "auto",
            "max_output_tokens": 300,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Обязательно выполни web search по официальному сайту OpenAI. "
                                "Ответь только: API_TEST_OK"
                            ),
                        }
                    ],
                }
            ],
        }
        result = self._create_response(payload)
        text = normalize_analysis(extract_output_text(result))
        if not text:
            raise RuntimeError("OpenAI returned no text in test response")
        return text, response_used_web_search(result)

    def _create_response(self, payload: dict) -> dict:
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
                context=self.ssl_context,
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI error {error.code}: {body[:500]}") from error
        return result

    def _build_prompt(self, signal) -> str:
        symbol = str(getattr(signal, "symbol", ""))
        ticker = symbol.removesuffix("USDT")
        timeframe_metrics = _format_timeframe_metrics(signal)
        return f"""
Ты независимый риск-аналитик криптовалютного фьючерсного сигнала на шорт.
Сначала через web search точно определи проект {ticker} ({symbol}), не перепутай
его с одноименным токеном. Проверь свежие новости, объявления проекта и бирж,
листинг/делистинг, разблокировки, взломы, судебные и регуляторные события.
Учитывай приложенный график и метрики скринера:
- источник: {getattr(signal, 'source', '')}
- модель: {getattr(signal, 'mode', '')}
- сила: {getattr(signal, 'signal_score', 0)}/10
- цена источника: {getattr(signal, 'price', 0):g}
- исполнимый вход Bybit SHORT (bid): {getattr(signal, 'entry_price', 0) or getattr(signal, 'price', 0):g}
- Bybit ask: {getattr(signal, 'entry_ask', 0):g}
- high разгона: {getattr(signal, 'high_price', 0):g}
- рост до разворота: {getattr(signal, 'price_growth_lookback_pct', 0):+.2f}%
- откат от high: {getattr(signal, 'drawdown_from_high_pct', 0):+.2f}%
- метрики по периодам:
{timeframe_metrics}
- funding: {_format_funding(signal)}

Дай сценарное мнение, не обещание результата. Не выдумывай новости. Если проект
или актуальный фон нельзя надежно подтвердить, прямо скажи это. Ответ только на
русском, без markdown, максимум 190 символов, строго в 3 коротких строках:
AI: SHORT / WAIT / NO TRADE, уверенность X/10
Фон: один главный подтвержденный фактор или «значимых новостей не найдено»
План: вход диапазоном; отмена; цели T1 и T2
""".strip()


def _format_timeframe_metrics(signal) -> str:
    timeframes = getattr(signal, "timeframes", ()) or ()
    if not timeframes:
        return (
            f"  1H: цена {getattr(signal, 'price_change_window_pct', 0):+.2f}%, "
            f"OI {getattr(signal, 'oi_change_pct', 0):+.2f}%, "
            f"futures CVD {getattr(signal, 'cvd_delta_usdt', 0):,.0f} USDT"
        )
    return "\n".join(
        f"  {timeframe.label}: цена {_optional_metric(timeframe.price_change_pct, '%')}, "
        f"OI {_optional_metric(timeframe.oi_change_pct, '%')}, "
        f"futures CVD {_optional_metric(timeframe.cvd_delta_usdt, ' USDT', digits=0)}"
        for timeframe in timeframes
    )


def _optional_metric(value, suffix: str, *, digits: int = 2) -> str:
    if value is None:
        return "нет данных"
    return f"{value:+,.{digits}f}{suffix}"


def _format_funding(signal) -> str:
    if not getattr(signal, "funding_available", True):
        return "нет данных"
    return f"{getattr(signal, 'funding_rate', 0) * 100:.4f}%"


def extract_output_text(response: dict) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    fragments: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                fragments.append(content["text"].strip())
    return "\n".join(fragment for fragment in fragments if fragment)


def extract_citation_urls(response: dict) -> list[str]:
    urls: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations", []):
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if isinstance(url, str) and url.startswith(("https://", "http://")) and url not in urls:
                    urls.append(url)
    return urls


def response_used_web_search(response: dict) -> bool:
    return any(
        isinstance(item, dict) and item.get("type") == "web_search_call"
        for item in response.get("output", [])
    )


def normalize_analysis(text: str) -> str:
    lines = []
    for raw_line in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip(" -*#")
        if line:
            lines.append(line)
    return "\n".join(lines)


def compose_enriched_caption(
    base_caption: str,
    analysis: str,
    *,
    limit: int = TELEGRAM_PHOTO_CAPTION_LIMIT,
) -> str:
    analysis = normalize_analysis(analysis)
    if not analysis:
        return base_caption[:limit]

    separator = "\n\n🤖 OpenAI + интернет:\n"
    available = limit - len(base_caption) - len(separator)
    if available <= 0:
        return base_caption[:limit]
    if len(analysis) > available:
        source_marker = "\nИсточник: "
        if source_marker in analysis:
            body, source = analysis.rsplit(source_marker, 1)
            source_line = f"{source_marker}{source}"
            body_available = available - len(source_line)
            if body_available > 1:
                body = body[: body_available - 1].rstrip() + "…"
                analysis = f"{body}{source_line}"
            else:
                analysis = source_line.lstrip()[:available]
        else:
            analysis = analysis[: max(0, available - 1)].rstrip() + "…"
    return f"{base_caption}{separator}{analysis}"
