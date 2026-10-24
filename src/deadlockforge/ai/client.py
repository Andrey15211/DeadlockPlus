"""Universal multi-model LLM client. stdlib urllib only, no SDK.

Supported providers (see create_client):
    deepseek, openai, anthropic.
(Gemini убран полностью по решению владельца: недоступен по API.)

All HTTP goes through urllib.request.Request + urlopen. Normal reports use
the 100s LLM_TIMEOUT; test_connection() overrides the per-client
request_timeout with the short TEST_TIMEOUT so a 'ping' can never hang the
/api/ai/test endpoint. Errors raise LLMError with a readable message and,
when available, the provider's JSON error body.
"""

from __future__ import annotations

import base64
import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..models import AIConfig

# V2.0 М4 + фикс «таймаут 200с»: glm-5.3-flash на роутере пользователя
# стабильно разбирает тяжёлый матч за ~63.5 с (thinking disabled, валидный
# CoachReport; замер match 113150202). glm-5.3 — 87.9 с (на грани).
# Лимит 120 с утверждён владельцем. Старый лимит 60/65 с резал валидные разборы.
# Потолок вывода LLM_MAX_TOKENS=8000: без него GLM пишет 7–10к токенов +
# reasoning и генерация уходит за 100–200с (логи владельца). С лаконичным
# промптом реальный ответ — 1–2к токенов (30–40с).
LLM_TIMEOUT = 180  # 3 minutes for heavy reasoning / deep diving models
LLM_MAX_TOKENS = 8000  # жёсткий потолок вывода: не даём раздувать ответ
TEST_TIMEOUT = 180  # /api/ai/test ping: 180s (3m) for heavy reasoning models on failover router
PING_TIMEOUT = 45   # короткий пинг модели: шлюз отвечает за 1–5с, медленные до 96с

DEFAULT_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

DEFAULT_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.6",
    "Connection": "close",
}

# V2.0 М5 (фикс «разбор дольше 60с»): потолок вывода — функция от уровня
# размышлений, а не константа. Жёсткие 16k токенов на reasoning-моделях
# (glm/kimi/deepseek-v4) давали 70–120с генерации даже в «Коротком» режиме,
# где thinking отключён. Лаконичный промпт реально даёт 1–2к токенов.
OUTPUT_TOKEN_BUDGET: dict[str, int] = {
    "off": 3000, "low": 6000, "medium": 10000, "high": 16000,
}


def output_token_budget(config) -> int:
    """Потолок вывода под выбранный reasoning_effort / enable_thinking."""
    effort = (getattr(config, "reasoning_effort", None) or "").strip().lower()
    if effort in OUTPUT_TOKEN_BUDGET:
        return OUTPUT_TOKEN_BUDGET[effort]
    if not getattr(config, "enable_thinking", True):
        return OUTPUT_TOKEN_BUDGET["off"]
    return OUTPUT_TOKEN_BUDGET["medium"]


# Шлюз Zexkora принимает reasoning_effort только из этого набора и валит запрос
# HTTP 400 «unknown variant `off`, expected one of none|minimal|...» на
# внутреннем значении «off» (профиль «Отключено»). Поэтому внутреннее «off»
# уходит в API как «none». Ключ thinking не отправляем: с ним
# deepseek-v4.1-flash отдаёт пустой content и/или висит до таймаута.
REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def reasoning_effort_value(config) -> str:
    """reasoning_effort в терминах API провайдера (никогда не «off»)."""
    effort = (getattr(config, "reasoning_effort", None) or "").strip().lower()
    if effort in ("", "auto"):
        return "low"
    return effort if effort in REASONING_EFFORTS else "none"


TRANSIENT_EXCEPTIONS = (
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    ConnectionError,
    TimeoutError,
    ssl.SSLEOFError,
)


def _is_transient_error(exc: BaseException, detail: str = "") -> bool:
    """Return True if exception is a transient network/proxy error eligible for retry."""
    if detail:
        lower_detail = detail.lower()
        if any(permanent in lower_detail for permanent in (
            "model_not_found", "invalid_api_key", "unsupported_model", "invalid_request_error"
        )):
            return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (502, 503, 504)
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _is_transient_error(reason, detail)
        reason_str = str(reason).lower()
        return any(term in reason_str for term in (
            "timed out", "remote end closed", "connection reset",
            "connection aborted", "forcibly closed", "broken pipe",
        ))
    if isinstance(exc, (http.client.HTTPException, OSError)):
        msg = str(exc).lower()
        return any(term in msg for term in (
            "remote end closed", "connection reset", "forcibly closed",
            "connection aborted", "timed out", "incomplete read",
            "eof occurred in violation of protocol", "10054", "10053", "10060",
        ))
    return False


def _is_timeout_error(exc: BaseException) -> bool:
    """True if the failure is a socket/reply timeout.

    Timeouts of LLM generation mean the model is still writing (slow, big
    output) — re-sending the very same request doubles the wall time (120+120
    = 240s) instead of failing over to the next model. Retry must skip these.
    """
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _is_timeout_error(reason)
        return "timed out" in str(reason).lower()
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg


class LLMError(RuntimeError):
    """Network / HTTP / schema errors while talking to the LLM provider."""


def _friendly_http_detail(e: urllib.error.HTTPError, detail: str) -> str:
    """Human-readable Russian detail for auth / quota / rate errors."""
    if e.code in (401, 403):
        return (
            f"Неверный API-ключ ({e.code}). Проверь ключ в настройках. "
            f"Ответ провайдера: {detail[:120]}"
        )
    if e.code == 429:
        return (
            f"Лимит запросов исчерпан (429). Подожди или проверь баланс. "
            f"Ответ провайдера: {detail[:120]}"
        )
    return ""


def _post_json(url: str, headers: dict[str, str], payload: dict,
               timeout: float | None = None,
               max_retries: int = 1,
               retry_delay: float = 1.0) -> dict:
    """POST JSON via urllib, return decoded JSON body. LLMError on failure."""
    timeout = LLM_TIMEOUT if timeout is None else timeout
    current_payload = dict(payload)
    req_headers = {"Content-Type": "application/json", **DEFAULT_REQUEST_HEADERS, **headers}

    for attempt in range(max_retries + 1):
        body = json.dumps(current_payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers=req_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise LLMError(f"LLM returned non-JSON body: {raw[:300]}") from e
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 - best-effort error body
                pass
            # Если провайдер/роутер не поддерживает response_format — удаляем и пробуем сразу
            if e.code == 400 and "response_format" in detail.lower() and "response_format" in current_payload:
                current_payload.pop("response_format", None)
                continue
            # Провайдер не знает reasoning_effort (plain-модели, сторонние шлюзы):
            # запрос без него валиден, модель отвечает.
            if e.code == 400 and "reasoning_effort" in detail.lower() and "reasoning_effort" in current_payload:
                current_payload.pop("reasoning_effort", None)
                continue
            if e.code == 400 and ("max_tokens" in detail.lower() or "max_completion_tokens" in detail.lower()):
                if "max_tokens" in current_payload:
                    current_payload["max_tokens"] = 2048
                if "max_completion_tokens" in current_payload:
                    current_payload["max_completion_tokens"] = 2048
                continue
            if _is_transient_error(e, detail) and not _is_timeout_error(e) and attempt < max_retries:
                time.sleep(retry_delay)
                continue
            friendly = _friendly_http_detail(e, detail)
            if friendly:
                raise LLMError(friendly) from e
            raise LLMError(f"LLM HTTP {e.code} from {url}: {detail}") from e
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            # Таймаут НЕ ретраим: модель уже генерила и, скорее всего, снова
            # упрётся в лимит; уходим на следующую модель из fallback-цепочки.
            if _is_transient_error(e) and not _is_timeout_error(e) and attempt < max_retries:
                time.sleep(retry_delay)
                continue
            if isinstance(e, urllib.error.URLError):
                raise LLMError(f"LLM request to {url} failed: {e.reason}") from e
            if isinstance(e, (TimeoutError, http.client.HTTPException, ConnectionError, OSError)):
                raise LLMError(
                    f"LLM request to {url} failed ({type(e).__name__}): {e}"
                ) from e
            raise


def _stream_chat_completions(url: str, headers: dict[str, str],
                             payload: dict, timeout: float,
                             on_chunk,
                             max_retries: int = 1,
                             retry_delay: float = 1.0) -> str:
    """POST with stream:true; уведомляет on_chunk(received_chars) и
    возвращает собранный текст. Линии 'data: {...}' SSE; пустые и
    'data: [DONE]' пропускаются.
    """
    stream_payload = dict(payload, stream=True)
    body = json.dumps(stream_payload).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **DEFAULT_REQUEST_HEADERS,
        **headers,
    }

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url, data=body,
            headers=req_headers,
            method="POST",
        )
        received = 0
        parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    piece = delta.get("content") or ""
                    reasoning_piece = delta.get("reasoning_content") or ""
                    if piece:
                        parts.append(piece)
                        received += len(piece)
                        if on_chunk:
                            on_chunk(received)
                    elif reasoning_piece:
                        # Thinking-модель шлюза может отдать ответ только здесь.
                        reasoning_parts.append(reasoning_piece)
                        if on_chunk:
                            on_chunk(received + max(1, len(reasoning_piece) // 4))

            result_text = "".join(parts)
            if not result_text.strip() and reasoning_parts:
                reasoning = "".join(reasoning_parts)
                obj = _extract_json_object(reasoning)
                result_text = json.dumps(obj, ensure_ascii=False) if obj is not None else reasoning
            if not result_text.strip():
                # If stream closed cleanly without emitting tokens, fetch non-streaming
                non_stream_payload = dict(stream_payload)
                non_stream_payload.pop("stream", None)
                data = _post_json(url, headers, non_stream_payload, timeout=timeout)
                result_text = _choice_content(data)
                if on_chunk and result_text:
                    on_chunk(len(result_text))
            return result_text

        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 - best-effort body
                pass
            if e.code == 400 and "response_format" in detail.lower() and "response_format" in stream_payload:
                stream_payload.pop("response_format", None)
                body = json.dumps(stream_payload).encode("utf-8")
                continue
            if e.code == 400 and "reasoning_effort" in detail.lower() and "reasoning_effort" in stream_payload:
                stream_payload.pop("reasoning_effort", None)
                body = json.dumps(stream_payload).encode("utf-8")
                continue
            if e.code == 400 and ("max_tokens" in detail.lower() or "max_completion_tokens" in detail.lower()):
                if "max_tokens" in stream_payload:
                    stream_payload["max_tokens"] = 2048
                if "max_completion_tokens" in stream_payload:
                    stream_payload["max_completion_tokens"] = 2048
                body = json.dumps(stream_payload).encode("utf-8")
                continue
            if _is_transient_error(e, detail) and not _is_timeout_error(e) and attempt < max_retries:
                time.sleep(retry_delay)
                continue
            friendly = _friendly_http_detail(e, detail)
            if friendly:
                raise LLMError(friendly) from e
            raise LLMError(f"LLM HTTP {e.code} from {url}: {detail}") from e
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            if _is_transient_error(e) and not _is_timeout_error(e):
                if not parts and attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                # Fallback to direct non-streaming POST if proxy SSE connection was dropped/interrupted
                try:
                    non_stream_payload = dict(payload)
                    non_stream_payload.pop("stream", None)
                    data = _post_json(url, headers, non_stream_payload, timeout=timeout)
                    text = _choice_content(data)
                    if on_chunk and text:
                        on_chunk(len(text))
                    return text
                except Exception as retry_exc:
                    if isinstance(retry_exc, LLMError):
                        raise
                    raise LLMError(f"LLM request to {url} failed: {retry_exc}") from retry_exc

            if isinstance(e, urllib.error.URLError):
                raise LLMError(f"LLM stream to {url} failed: {e.reason}") from e
            if isinstance(e, (TimeoutError, http.client.HTTPException, ConnectionError, OSError)):
                raise LLMError(f"LLM stream to {url} failed ({type(e).__name__}): {e}") from e
            raise


class LLMClient(ABC):
    """Base client: generate_coach_report returns the raw LLM text/JSON."""

    provider: str = ""
    supports_vision: bool = False
    supports_thinking: bool = False

    def __init__(self, config: AIConfig) -> None:
        self.config = config
        self.base_url = (
            config.base_url or DEFAULT_BASE_URLS.get(self.provider, "")
        ).rstrip("/")
        # Per-request timeout; test_connection() shrinks it to TEST_TIMEOUT.
        self.request_timeout: float = LLM_TIMEOUT

    @abstractmethod
    def generate_coach_report(self, prompt: str, match_data: dict,
                              image_bytes: bytes | None = None) -> str:
        """Send the prompt (+ optional image) and return the raw model text."""

    def generate_coach_report_stream(self, prompt: str, match_data: dict,
                                     image_bytes: bytes | None = None,
                                     on_chunk=None) -> str:
        """Streaming вариант. Базовая реализация — не-стриминг вызов с
        одним итоговым on_chunk; провайдеры переопределяют."""
        raw = self.generate_coach_report(prompt, match_data,
                                         image_bytes=image_bytes)
        if on_chunk and raw:
            on_chunk(len(raw))
        return raw

    def _chat_payload(self, prompt: str, match_data: dict,
                      image_bytes: bytes | None = None) -> tuple[dict, list[dict]]:
        """Общий payload/messages для chat/completions-провайдеров."""
        model = self._resolve_model() if hasattr(self, "_resolve_model") else self.config.model_name
        text = (prompt + "\n\nДанные матча (JSON):\n"
                + json.dumps(match_data, ensure_ascii=False, default=str)[:60000])
        b64 = self._image_b64(image_bytes)
        if b64 is not None:
            content: list[dict] = [{"type": "text", "text": text}]
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": text}]

        payload: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": min(LLM_MAX_TOKENS, output_token_budget(self.config)),
            "response_format": {"type": "json_object"},
        }
        return payload, messages

    def _image_b64(self, image_bytes: bytes | None) -> Optional[str]:
        if image_bytes is None:
            return None
        return base64.b64encode(image_bytes).decode("ascii")


class DeepSeekClient(LLMClient):
    provider = "deepseek"
    supports_vision = False
    supports_thinking = True

    def generate_coach_report(self, prompt: str, match_data: dict,
                              image_bytes: bytes | None = None) -> str:
        payload, _ = self._chat_payload(prompt, match_data, image_bytes)
        if "reasoner" in self.config.model_name:
            # deepseek-reasoner: reasoning_content comes back separately.
            if self.config.enable_thinking:
                payload["enable_thinking"] = True
        else:
            payload["temperature"] = self.config.temperature
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = _post_json(f"{self.base_url}/chat/completions", headers, payload,
                          timeout=self.request_timeout)
        return _choice_content(data)

    def generate_coach_report_stream(self, prompt: str, match_data: dict,
                                     image_bytes: bytes | None = None,
                                     on_chunk=None) -> str:
        """Streaming-вызов (stream:true, SSE-chunks data: {...})."""
        payload, _ = self._chat_payload(prompt, match_data, image_bytes)
        if "reasoner" not in self.config.model_name:
            payload["temperature"] = self.config.temperature
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return _stream_chat_completions(
            f"{self.base_url}/chat/completions", headers, payload,
            timeout=self.request_timeout, on_chunk=on_chunk)


class OpenAIClient(LLMClient):
    provider = "openai"
    supports_vision = True
    supports_thinking = False

    def _resolve_model(self) -> str:
        model = self.config.model_name
        # OpenAIClient обслуживает роутеры / v1-эндпоинты (локальный 127.0.0.1:8089,
        # zexkora и т.п.). Легаси-имена gemini-* и пустая модель резолвятся
        # в рабочую deepseek-v4.1-flash.
        _LEGACY_BROKEN = (
            "default", "", "gemini-2.5-flash", "gemini-2.0-flash",
            "gemini-3.8-flash", "gemini-3.8-flash-low",
        )
        if "generativelanguage.googleapis.com" not in self.base_url:
            if not model or model in _LEGACY_BROKEN:
                return "deepseek-v4.1-flash"
        return model or "deepseek-v4.1-flash"

    def _is_reasoning_model(self) -> bool:
        name = self._resolve_model().lower()
        return (
            name.startswith(("o1", "o3", "o4"))
            or bool(name.startswith("o") and name[1:2].isdigit())
            or "-reasoning" in name
            or "-sol" in name
            or "reasoner" in name
            or "deepseek-v4" in name
            or "deepseek-r1" in name
            or "opus-5-thinking" in name
            or "qwq" in name
            or "glm" in name
            or "kimi" in name
            or "minimax" in name
        )

    def generate_coach_report(self, prompt: str, match_data: dict,
                              image_bytes: bytes | None = None) -> str:
        text = (prompt + "\n\nДанные матча (JSON):\n"
                + json.dumps(match_data, ensure_ascii=False, default=str)[:60000])
        b64 = self._image_b64(image_bytes)
        if b64 is not None:
            content: list[dict] = [{"type": "text", "text": text}]
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": text}]

        payload: dict = {
            "model": self._resolve_model(),
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        effort = reasoning_effort_value(self.config)
        budget = output_token_budget(self.config)

        if self._is_reasoning_model():
            # o-models / deepseek reasoning / glm / kimi / minimax: max_completion_tokens
            payload["max_completion_tokens"] = budget
        else:
            payload["temperature"] = self.config.temperature
            payload["max_tokens"] = min(LLM_MAX_TOKENS, budget)
        # «off» шлюз отвергает HTTP 400 — наружу уходит «none» (см.
        # reasoning_effort_value); thinking не отправляем вовсе.
        payload["reasoning_effort"] = effort

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = _post_json(f"{self.base_url}/chat/completions", headers, payload,
                          timeout=self.request_timeout)
        content = _choice_content(data)
        if not content.strip():
            # Пустой content на длинном JSON-промпте: ретрай без response_format
            # и без размышлений (лечит сбой роутера на thinking-моделях).
            retry = dict(payload)
            retry.pop("response_format", None)
            retry["reasoning_effort"] = "none"
            data = _post_json(f"{self.base_url}/chat/completions", headers, retry,
                              timeout=self.request_timeout)
            content = _choice_content(data)
        return content

    def generate_coach_report_stream(self, prompt: str, match_data: dict,
                                     image_bytes: bytes | None = None,
                                     on_chunk=None) -> str:
        """Streaming для OpenAI-совместимых (наследует payload-логику)."""
        text = (prompt + "\n\nДанные матча (JSON):\n"
                + json.dumps(match_data, ensure_ascii=False, default=str)[:60000])
        b64 = self._image_b64(image_bytes)
        if b64 is not None:
            messages = [{"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}]
        else:
            messages = [{"role": "user", "content": text}]
        payload = {"model": self._resolve_model(), "messages": messages,
                   "response_format": {"type": "json_object"},
                   "stream": True}
        effort = reasoning_effort_value(self.config)
        budget = output_token_budget(self.config)

        if self._is_reasoning_model():
            payload["max_completion_tokens"] = budget
        else:
            payload["temperature"] = self.config.temperature
            payload["max_tokens"] = min(LLM_MAX_TOKENS, budget)
        payload["reasoning_effort"] = effort

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return _stream_chat_completions(
            f"{self.base_url}/chat/completions", headers, payload,
            timeout=self.request_timeout, on_chunk=on_chunk)


class AnthropicClient(LLMClient):
    provider = "anthropic"
    supports_vision = True
    supports_thinking = True

    def generate_coach_report(self, prompt: str, match_data: dict,
                              image_bytes: bytes | None = None) -> str:
        text = (prompt + "\n\nДанные матча (JSON):\n"
                + json.dumps(match_data, ensure_ascii=False, default=str)[:60000])
        content: list[dict] = []
        b64 = self._image_b64(image_bytes)
        if b64 is not None:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": text})

        max_tokens = 8000
        payload: dict = {
            "model": self.config.model_name,
            "max_tokens": max_tokens,
            "temperature": self.config.temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if self.config.enable_thinking:
            budget = min(self.config.thinking_budget, 8000)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # max_tokens must exceed budget_tokens.
            payload["max_tokens"] = max_tokens + budget

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = _post_json(f"{self.base_url}/v1/messages", headers, payload,
                          timeout=self.request_timeout)
        try:
            blocks = data["content"]
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected anthropic response: {json.dumps(data)[:300]}") from e
        return "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        )


def _choice_content(data: dict) -> str:
    """Extract content from a chat/completions response (deepseek/openai shape)."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected chat response: {json.dumps(data)[:300]}") from e
    # deepseek-reasoner may return reasoning_content separately: take content.
    content = message.get("content")
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = content or ""
    if not text.strip():
        # Thinking-модель шлюза (deepseek-v4*) иногда отдаёт весь ответ в
        # reasoning_content, а content оставляет пустым. Без этого разбор
        # падал как «модель не ответила» на валидном ответе.
        reasoning = message.get("reasoning_content") or ""
        if isinstance(reasoning, str) and reasoning.strip():
            obj = _extract_json_object(reasoning)
            text = json.dumps(obj, ensure_ascii=False) if obj is not None else reasoning
    return text


_CLIENT_CLASSES: dict[str, type[LLMClient]] = {
    "deepseek": DeepSeekClient,
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
}


def create_client(config: AIConfig) -> LLMClient:
    """Factory: pick the client class by config.provider. ValueError otherwise."""
    provider = config.provider.strip().lower()
    base_url = (config.base_url or "").rstrip("/")

    # Любой кастомный роутер или эндпоинт со стандартным /v1 (api.zxc1x1.ru/v1, api.zexkora.cc/v1,
    # локальный failover 127.0.0.1:8089/v1 и др.)
    if base_url and "generativelanguage.googleapis.com" not in base_url:
        if (
            base_url.endswith("/v1")
            or "/v1" in base_url
            or "zxc1x1" in base_url
            or "zexkora" in base_url
            or provider in ("openai", "router", "openrouter")
        ):
            return OpenAIClient(config)

    cls = _CLIENT_CLASSES.get(provider)
    if cls is None:
        raise ValueError(
            f"unknown provider '{config.provider}', expected one of "
            f"{sorted(_CLIENT_CLASSES)}"
        )
    return cls(config)


def _ping_chat(base_url: str, api_key: str, mid: str,
               timeout: float = PING_TIMEOUT) -> tuple[bool, str]:
    """Минимальный живой запрос к модели: (ответила ли, текст/ошибка).

    Полный payload разбора (response_format + бюджет в тысячи токенов) на
    «проверке соединения» вис до 165с и падал по таймауту, хотя канал жив:
    шлюз отвечает на короткий запрос за секунды.
    """
    payload = {"model": mid, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 16, "reasoning_effort": "none"}
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json",
               **DEFAULT_REQUEST_HEADERS}
    for attempt in range(2):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return True, _choice_content(json.loads(raw))[:120]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 — тело ошибки best-effort
                pass
            # Провайдер не знает reasoning_effort — повторяем без него.
            if e.code == 400 and "reasoning_effort" in detail.lower() and attempt == 0:
                payload.pop("reasoning_effort", None)
                continue
            friendly = _friendly_http_detail(e, detail)
            return False, friendly or f"HTTP {e.code}: {detail}"
        except Exception as e:  # noqa: BLE001 — таймаут/сеть: модель не ответила
            return False, f"{type(e).__name__}: {e}"[:200]
    return False, "ping failed"


def test_connection(config: AIConfig) -> dict:
    """Проверка канала: параллельный короткий пинг выбранной и резервных моделей.

    Возвращает {ok, latency_ms, detail, model}. Шлюз может держать отдельную
    модель на тех. работах (deepseek-v4.1-flash висел >45с при живом
    glm-5.3-flash) — тогда соединение с нейросетью есть, и это надо показать,
    а не рапортовать «нет соединения».
    """
    try:
        client = create_client(config)
    except ValueError as e:
        return {"ok": False, "latency_ms": 0, "detail": str(e), "model": config.model_name}

    if isinstance(client, AnthropicClient):
        # Anthropic-совместимый эндпоинт: свой путь /v1/messages.
        client.request_timeout = TEST_TIMEOUT
        start = time.monotonic()
        try:
            raw = client.generate_coach_report(
                "Generate a simple json response with key result and value active",
                {"ping": True})
            return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": raw[:200], "model": config.model_name}
        except Exception as e:  # noqa: BLE001 — ping never raises out
            return {"ok": False, "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": str(e)[:300], "model": config.model_name}

    base_url = client.base_url
    chain: list[str] = []
    for mid in (config.model_name, *VERIFIED_CHAT_MODELS):
        if mid and mid not in chain:
            chain.append(mid)

    start = time.monotonic()
    # Параллельно и с ранним выходом: последовательный обход 6 моделей давал до
    # 2 минут ожидания в UI, а ответ первой живой модели закрывает проверку.
    errors: dict[str, str] = {}
    executor = ThreadPoolExecutor(max_workers=min(6, len(chain)))
    try:
        futures = {mid: executor.submit(_ping_chat, base_url, config.api_key, mid)
                   for mid in chain}
        # Выбранная модель решает первой: иначе более быстрый резерв отвечает
        # раньше и проверка ложно рапортует «выбранная не ответила».
        ok, detail = futures[config.model_name].result()
        if ok:
            return {"ok": True,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": detail or "active", "model": config.model_name}
        errors[config.model_name] = detail

        for mid in chain[1:]:
            ok, detail = futures[mid].result()
            if not ok:
                errors[mid] = detail
                continue
            return {"ok": True,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": f"{detail or 'active'} (выбранная "
                              f"«{config.model_name}» не ответила — работает «{mid}»)",
                    "model": mid}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return {"ok": False, "latency_ms": int((time.monotonic() - start) * 1000),
            "detail": "Ни одна модель не ответила. "
                      + "; ".join(f"{m}: {errors[m]}" for m in chain if m in errors)[:400],
            "model": config.model_name}


def _extract_json_object(raw: str) -> dict | None:
    """Первый полный JSON-объект из тела ответа.

    Регрессия (жалоба «Рабочие модели не найдены»): роутер zexkora отдаёт
    валидный JSON, а СРАЗУ ЗА НИМ дописывает SSE-хвост:
        {"data":[...],"object":"list","success":true}
        data: {"choices":[...],"id":"chatcmpl-stream",...}
        data: [DONE]
    json.loads() падал с «Extra data: line 2 column 1», список моделей
    оказывался пустым. Читаем объект по балансу скобок до первого мусора.
    """
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


# Резервная цепочка чат-моделей шлюза (живой пинг 2026-10-22): нужна только
# для failover разбора и пинга соединения. Каталог в профиле строится из
# реального ответа провайдера.
VERIFIED_CHAT_MODELS: tuple[str, ...] = (
    "deepseek-v4.1-flash", "glm-5.3-flash", "kimi-k3",
    "deepseek-v4-flash", "minimax-m3", "deepseek-v4-pro",
)

PORTAL_CATALOG_URL = "https://api.zexkora.cc/portal-api/models"
GATEWAY_MODELS_URL = "https://api.zexkora.cc/v1/models"


def _is_gateway(base_url: str) -> bool:
    """Эндпоинт — шлюз Zexkora или его локальный failover-прокси.

    Локальный прокси (127.0.0.1:8089) отдаёт урезанный /v1/models: glm, kimi,
    minimax и Qwen на нём работают, но в списке отсутствуют. Поэтому для шлюза
    каталог берётся у самого провайдера.
    """
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    return "zexkora" in host or host in ("127.0.0.1", "localhost", "::1")


def _portal_catalog(base_url: str, api_key: str) -> list[dict]:
    """Каталог моделей из кабинета провайдера (portal-api/models).

    /v1/models отдаёт надмножество: служебные tk/*-каналы, безымянные записи и
    модели вне группы ключа. Портал — то, что клиенту реально доступно:
    имя, вендор, вид (чат/картинки) и статус.
    """
    if not _is_gateway(base_url):
        return []
    req = urllib.request.Request(
        PORTAL_CATALOG_URL, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — кабинет недоступен: работаем по /v1/models
        return []
    return [m for m in data.get("models") or [] if isinstance(m, dict)]


def _catalog_ids(base_url: str, api_key: str, headers: dict[str, str]) -> list[str]:
    """id чат-моделей: кабинет провайдера + /v1/models.

    Кабинет — это оплаченный каталог группы ключа, но он отстаёт от эндпоинта
    (показывает claude-4.8 «активна», когда тот отдаёт 503, и не показывает
    glm/kimi/minimax/Qwen, которые реально отвечают). Поэтому берём оба
    источника, а решает живой пинг: он же отбрасывает мёртвые.
    """
    ids: list[str] = []
    for item in _portal_catalog(base_url, api_key):
        name = str(item.get("name") or "").strip()
        low = name.lower()
        if not name or low.startswith("tk/") or "image" in low:
            continue
        if (item.get("kind") or "chat") != "chat":
            continue
        ids.append(name)
    listed = base_url
    if _is_gateway(base_url) and "zexkora" not in base_url:
        # Локальный прокси: полный список моделей спрашиваем у провайдера,
        # пинг при этом идёт по настроенному base_url.
        listed = GATEWAY_MODELS_URL.rsplit("/models", 1)[0]
    try:
        for mid in _model_ids(listed, headers):
            if mid.lower() not in {m.lower() for m in ids}:
                ids.append(mid)
    except Exception:  # noqa: BLE001 — /v1/models недоступен: остаётся кабинет
        pass
    return ids


def _model_ids(base_url: str, headers: dict[str, str]) -> list[str]:
    """id чат-моделей из GET /v1/models (устойчиво к SSE-хвосту шлюза)."""
    req = urllib.request.Request(f"{base_url}/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", "replace")
    data = _extract_json_object(raw)
    if data is None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
    ids: list[str] = []
    for item in data.get("data") or data.get("models") or []:
        mid = (item.get("id") or item.get("name") or "") if isinstance(item, dict) else str(item)
        mid = mid.strip()
        low = mid.lower()
        # Пустые id, служебные tk/* и картинко-модели — не чат-модели.
        if not mid or low.startswith("tk/") or "image" in low:
            continue
        ids.append(mid)
    return ids


def _probe_model(base_url: str, headers: dict[str, str], mid: str,
                 timeout: float = 20.0) -> str:
    """Живой пинг chat/completions: 'ok' | 'slow' | 'dead'.

    max_tokens=1 шлюз валит 502 на части моделей (mimo-v2.6-flash), из-за чего
    рабочая модель пропадала из каталога — пингуем реалистичным минимумом.
    Таймаут ≠ смерть: deepseek-v4.1-flash по метрикам кабинета отвечает за
    ~96с, и выкидывать его за медлительность нельзя — только явный отказ
    (5xx/4xx) считается мёртвым.
    """
    payload = {"model": mid, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 16, "reasoning_effort": "none"}
    for attempt in range(2):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return "ok" if resp.status == 200 else "dead"
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 — тело ошибки best-effort
                pass
            # OpenAI и др. не знают reasoning_effort: повтор без него, иначе
            # живая модель получила бы ложный «dead».
            if e.code == 400 and "reasoning_effort" in detail.lower() and attempt == 0:
                payload.pop("reasoning_effort", None)
                continue
            return "dead"
        except Exception:  # noqa: BLE001 — таймаут/сеть: модель медленная, но живая
            return "slow"
    return "dead"


def list_available_models(config: AIConfig, only_working: bool = True) -> list[str]:
    """Реальный каталог чат-моделей: кабинет провайдера + живой пинг каждой.

    Источник — portal-api/models (то, что клиенту доступно), при недоступности
    кабинета — /v1/models. Шлюз отдаёт имена в разном регистре
    (DeepSeek-V4.1-Flash и deepseek-v4.1-flash — один эндпоинт), поэтому
    пингуется каждый вариант написания, а в каталог попадает ответивший;
    дубли по нижнему регистру схлопываются. Зашитых списков нет: каталог
    недоступен → пустой список, а не выдуманные модели.
    """
    if not config.api_key:
        return []

    provider = config.provider.strip().lower()
    base_url = (config.base_url or "").rstrip("/") or DEFAULT_BASE_URLS.get(
        provider, "https://api.openai.com/v1")
    headers = {"Authorization": f"Bearer {config.api_key}", **DEFAULT_REQUEST_HEADERS}

    try:
        ids = _catalog_ids(base_url, config.api_key, headers)
    except Exception:  # noqa: BLE001 — каталог недоступен
        return []
    if not ids:
        return []

    variants: dict[str, list[str]] = {}
    for mid in ids:
        bucket = variants.setdefault(mid.lower(), [])
        if mid not in bucket:
            bucket.append(mid)

    if not only_working:
        return [bucket[0] for bucket in variants.values()]

    probes = [(low, mid) for low, bucket in variants.items() for mid in bucket]
    with ThreadPoolExecutor(max_workers=min(16, len(probes))) as executor:
        states = dict(zip(
            (mid for _, mid in probes),
            executor.map(lambda pair: _probe_model(base_url, headers, pair[1]), probes)))

    # Каноническое написание — строчное: его используют дефолты и failover-цепочка.
    # Если строчный вариант мёртв, в каталог идёт тот, что ответил.
    result: list[str] = []
    for low, bucket in variants.items():
        working = [m for m in bucket if states.get(m) != "dead"]
        if working:
            result.append(low if low in working else working[0])
    return result
