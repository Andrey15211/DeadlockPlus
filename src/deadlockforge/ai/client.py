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
import urllib.request
from abc import ABC, abstractmethod
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
                    elif reasoning_piece and on_chunk:
                        on_chunk(received + max(1, len(reasoning_piece) // 4))

            result_text = "".join(parts)
            if not result_text:
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
        # V2.0 М4 (фикс «ИИ не работает после 65 секунд»): OpenAIClient
        # обслуживает роутеры / v1-эндпоинты (локальный 127.0.0.1:8089,
        # zexkora и т.п.). Легаси-gemini имена в БД (gemini-2.5-flash,
        # gemini-3.8-flash*) — устаревшие остатки старых настроек: роутер их
        # не знает и разбор падал по таймауту. На роутере резолвим их в
        # рабочую модель glm-5.3 (бенчмарк: 67 с, валидный отчёт).
        _LEGACY_BROKEN = ("default", "", "gemini-2.5-flash", "gemini-2.0-flash",
                          "gemini-3.8-flash", "gemini-3.8-flash-low")
        if "generativelanguage.googleapis.com" not in self.base_url:
            if not model or model in _LEGACY_BROKEN:
                return "glm-5.3-flash"
        return model or "glm-5.3-flash"

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
        effort = getattr(self.config, "reasoning_effort", None)
        enable_thinking = getattr(self.config, "enable_thinking", True)
        if effort in ("low", "medium", "high"):
            enable_thinking = True
        budget = output_token_budget(self.config)

        if self._is_reasoning_model():
            # o-models / deepseek reasoning / glm / kimi / minimax: max_completion_tokens
            payload["max_completion_tokens"] = budget
            if effort in ("low", "medium", "high"):
                payload["reasoning_effort"] = effort
            elif not enable_thinking or effort == "off":
                payload["reasoning_effort"] = "off"
                payload["thinking"] = {"type": "disabled"}
        else:
            payload["temperature"] = self.config.temperature
            payload["max_tokens"] = min(LLM_MAX_TOKENS, budget)
            if effort in ("low", "medium", "high"):
                payload["reasoning_effort"] = effort
            elif not enable_thinking or effort == "off":
                payload["thinking"] = {"type": "disabled"}
                payload["reasoning_effort"] = "off"

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = _post_json(f"{self.base_url}/chat/completions", headers, payload,
                          timeout=self.request_timeout)
        content = _choice_content(data)
        if not content.strip():
            # Пустой content на длинном JSON-промпте: ретрай без response_format
            # и с отключённым thinking (лечит сбой роутера).
            retry = dict(payload)
            retry.pop("response_format", None)
            retry["thinking"] = {"type": "disabled"}
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
        effort = getattr(self.config, "reasoning_effort", None)
        enable_thinking = getattr(self.config, "enable_thinking", True)
        if effort in ("low", "medium", "high"):
            enable_thinking = True
        budget = output_token_budget(self.config)

        if self._is_reasoning_model():
            payload["max_completion_tokens"] = budget
            if effort in ("low", "medium", "high"):
                payload["reasoning_effort"] = effort
            elif not enable_thinking or effort == "off":
                payload["reasoning_effort"] = "off"
                payload["thinking"] = {"type": "disabled"}
        else:
            payload["temperature"] = self.config.temperature
            payload["max_tokens"] = min(LLM_MAX_TOKENS, budget)
            if effort in ("low", "medium", "high"):
                payload["reasoning_effort"] = effort
            elif not enable_thinking or effort == "off":
                payload["thinking"] = {"type": "disabled"}
                payload["reasoning_effort"] = "off"

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
    return content or ""


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


def test_connection(config: AIConfig) -> dict:
    """Bounded 'ping': tiny prompt, TEST_TIMEOUT, -> {ok, latency_ms, detail}.

    Never raises and never hangs: any LLMError (auth, quota, timeout,
    network) becomes a structured {ok: False, latency_ms, detail}.
    """
    try:
        client = create_client(config)
    except ValueError as e:
        return {"ok": False, "latency_ms": 0, "detail": str(e)}

    client.request_timeout = TEST_TIMEOUT
    start = time.monotonic()
    try:
        # Reuse the provider call path with a tiny prompt: monkeypatching the
        # payload would fight every provider's shape, so we send the real
        # request but with a minimal prompt and truncated match_data.
        raw = client.generate_coach_report(
            "Generate a simple json response with key result and value active", {"ping": True}
        )
        latency = int((time.monotonic() - start) * 1000)
        return {"ok": True, "latency_ms": latency, "detail": raw[:200]}
    except LLMError as e:
        latency = int((time.monotonic() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "detail": str(e)[:300]}
    except Exception as e:  # noqa: BLE001 — ping must never raise out
        latency = int((time.monotonic() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "detail": f"{type(e).__name__}: {e}"[:300]}


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


def list_available_models(config: AIConfig, only_working: bool = True) -> list[str]:
    """Модели из /v1/models (router/OpenAI-compatible), устойчиво к SSE-хвосту.

    Возвращает РАБОЧИЕ модели (латенси-проверка) впереди; если пинг не дал
    ни одной живой (роутер «остыл», 429 у всех) — отдаём ВЕСЬ список, чтобы
    пользователь мог сменить модель вручную. Пустой список означает, что
    эндпоинт не ответил вовсе.
    """
    if not config.api_key:
        return []

    base_url = (config.base_url or "").rstrip("/")
    provider = config.provider.strip().lower()

    # Determine base url if not set
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "https://api.openai.com/v1")

    headers = {**DEFAULT_REQUEST_HEADERS}
    models: list[str] = []

    try:
        # OpenAI-compatible / router (e.g. 127.0.0.1:8089/v1, zexkora, openrouter)
        url = f"{base_url}/models"
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = _extract_json_object(raw)
        if data is None:
            # Классический json.loads как запасной путь (без SSE-хвоста)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return []
        items = data.get("data") or data.get("models") or []
        for item in items:
            mid = item.get("id") or item.get("name") if isinstance(item, dict) else str(item)
            if mid:
                models.append(mid)

    except Exception:  # noqa: BLE001 - return empty on any network/format error
        return []

    unique_models = sorted(set(models))
    if not only_working or not unique_models:
        return unique_models

    # Fast parallel health-check for OpenAI-compatible models (max 14 parallel threads, 10s timeout)
    from concurrent.futures import ThreadPoolExecutor

    def probe_model(mid: str) -> Optional[tuple[str, float]]:
        t_url = f"{base_url}/chat/completions"
        t_headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_REQUEST_HEADERS["User-Agent"],
        }
        # Lightweight ping: 1 token output
        t_data = json.dumps({
            "model": mid,
            "messages": [{"role": "user", "content": "1"}],
            "max_tokens": 1,
        }).encode("utf-8")
        t_req = urllib.request.Request(t_url, data=t_data, headers=t_headers)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(t_req, timeout=10) as resp:
                if resp.status == 200:
                    return (mid, time.monotonic() - t0)
        except Exception:
            pass
        return None

    try:
        with ThreadPoolExecutor(max_workers=min(14, len(unique_models))) as executor:
            working_entries = [res for res in executor.map(probe_model, unique_models) if res]
        if working_entries:
            # Sort by response latency: fast, cheap models appear first
            working_entries.sort(key=lambda x: x[1])
            return [m[0] for m in working_entries]
    except Exception:
        pass

    # Ни одна модель не ответила (все «остывают») — отдаём полный список,
    # иначе пользователь не сможет сменить модель вручную.
    return unique_models
