"""Тесты AI-стека: фабрика клиентов, payload каждого провайдера
(URL/заголовки/тело, vision base64), ошибки 401/429, coach.analyze_match
(валидный/невалидный JSON, обёртка ```json```, fallback). Только моки urlopen."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.ai import client as cl  # noqa: E402
from deadlockforge.ai import coach as coach_mod  # noqa: E402
from deadlockforge.engine import match_loader as ml  # noqa: E402
from deadlockforge.models import AIConfig, CoachReport  # noqa: E402


# --- helpers ---------------------------------------------------------------


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: bytes = b""):
        super().__init__("https://x", code, "msg", hdrs=None, fp=None)
        self._body = body

    def read(self) -> bytes:
        return self._body


def _mock_urlopen(monkeypatch, capture: dict, response: dict | None = None,
                  error: urllib.error.HTTPError | None = None):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return json.dumps(response or {}).encode()

    def fake(req, timeout=None):
        capture["req"] = req
        capture["timeout"] = timeout
        capture["body"] = json.loads(req.data.decode())
        if error is not None:
            raise error
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)


def _cfg(provider: str, **kw) -> AIConfig:
    defaults = dict(
        provider=provider,
        model_name="test-model",
        api_key="k-123",
        temperature=0.2,
        enable_thinking=True,
        thinking_budget=2048,
    )
    defaults.update(kw)
    return AIConfig(**defaults)


def _chat_response(content: str = '{"ok": true}') -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _normalized_match():
    raw = {
        "match_id": 1,
        "match_info": {
            "duration_s": 2400,
            "winning_team": 0,
            "team_score": {},
            "mid_boss": [{"game_time_s": 900, "team": 1}],
            "objectives": [{"game_time_s": 780, "type": "walker", "team": 0}],
            "players": [
                {
                    "account_id": 111, "player_slot": 0, "team": 0, "hero_id": 1,
                    "kills": 14, "deaths": 3, "assists": 9, "level": 25,
                    "net_worth": 82000,
                    "items": [
                        {"game_time_s": 120, "item_id": 101, "sold_time_s": 0},
                        {"game_time_s": 600, "item_id": 102, "sold_time_s": 0},
                        {"game_time_s": 1500, "item_id": 103, "sold_time_s": 1600},
                    ],
                    "death_details": [
                        {"game_time_s": 300, "killer_player_slot": 5,
                         "death_pos": {"x": 10752, "y": -10752}},
                        {"game_time_s": 700, "killer_player_slot": 5,
                         "death_pos": {"x": 0, "y": 0}},
                    ],
                    "stats": [
                        {"time_stamp_s": 0, "net_worth": 0},
                        {"time_stamp_s": 180, "net_worth": 5000},
                        {"time_stamp_s": 360, "net_worth": 11000},
                        {"time_stamp_s": 540, "net_worth": 17000},
                    ],
                },
                {
                    "account_id": 222, "player_slot": 5, "team": 1, "hero_id": 2,
                    "kills": 3, "deaths": 14, "assists": 2, "level": 21,
                    "net_worth": 64000,
                    "items": [{"game_time_s": 300, "item_id": 201, "sold_time_s": 0}],
                    "death_details": [],
                    "stats": [
                        {"time_stamp_s": 0, "net_worth": 0},
                        {"time_stamp_s": 180, "net_worth": 4000},
                        {"time_stamp_s": 360, "net_worth": 8500},
                        {"time_stamp_s": 540, "net_worth": 13000},
                    ],
                },
            ],
        },
    }
    return ml.normalize_match(raw, {1: "infernus", 2: "abrams"})


VALID_REPORT = {
    "performance_grade": "A",
    "key_mistake": {
        "time": "05:00", "location": "оранжевая линия",
        "event": "смерть без вижена", "why_fatal": "отдал темп",
        "correct_play": "отступить и отфармить",
    },
    "coach_conclusion": ["раз", "два", "три"],
    "benchmarks": {"souls_at_10": 0.5, "lane_efficiency": 0.6,
                   "fight_participation": 0.7},
    "item_corrections": [
        {"slot": "weapon-2", "sold_or_skipped": "ср. заряд",
         "recommended": "урон", "reason": "против брони"},
    ],
}


class _StubClient:
    """Мок LLMClient для coach-тестов."""

    def __init__(self, response: str | Exception):
        self.response = response
        self.calls: list[dict] = []

    def generate_coach_report(self, prompt, match_data, image_bytes=None):
        self.calls.append({
            "prompt": prompt, "match_data": match_data,
            "image_bytes": image_bytes,
        })
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


# --- factory ---------------------------------------------------------------


def test_create_client_dispatch():
    assert isinstance(cl.create_client(_cfg("deepseek")), cl.DeepSeekClient)
    assert isinstance(cl.create_client(_cfg("openai")), cl.OpenAIClient)
    assert isinstance(cl.create_client(_cfg("anthropic")), cl.AnthropicClient)


def test_create_client_wrong_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        cl.create_client(_cfg("groq"))


# --- deepseek --------------------------------------------------------------


def test_deepseek_payload_and_parse(monkeypatch):
    cap: dict = {}
    _mock_urlopen(monkeypatch, cap, response=_chat_response())
    client = cl.create_client(_cfg("deepseek", model_name="deepseek-v4.1-flash"))
    out = client.generate_coach_report("разбор", {"kda": "14/3/9"})
    assert out == '{"ok": true}'
    assert cap["req"].full_url == "https://api.deepseek.com/chat/completions"
    assert cap["req"].headers.get("Authorization") == "Bearer k-123"
    body = cap["body"]
    assert body["response_format"] == {"type": "json_object"}
    # V2.0 М5: потолок вывода — от уровня размышлений (дефолт low → 6000)
    assert body["max_tokens"] == cl.OUTPUT_TOKEN_BUDGET["low"]
    assert body["temperature"] == 0.2
    assert body["model"] == "deepseek-v4.1-flash"
    assert cap["timeout"] == cl.LLM_TIMEOUT


def test_deepseek_reasoner_thinking_flag(monkeypatch):
    cap: dict = {}
    resp = {"choices": [{"message": {
        "role": "assistant", "content": '{"ok": true}',
        "reasoning_content": "долгие рассуждения",
    }}]}
    _mock_urlopen(monkeypatch, cap, response=resp)
    client = cl.create_client(_cfg("deepseek", model_name="deepseek-reasoner"))
    out = client.generate_coach_report("p", {})
    assert out == '{"ok": true}'  # content, не reasoning_content
    assert cap["body"]["enable_thinking"] is True
    assert "temperature" not in cap["body"]


# --- openai ----------------------------------------------------------------


def test_openai_payload_plain_model(monkeypatch):
    cap: dict = {}
    _mock_urlopen(monkeypatch, cap, response=_chat_response())
    client = cl.create_client(_cfg("openai", model_name="gpt-6-astra"))
    out = client.generate_coach_report("разбор", {"a": 1})
    assert out == '{"ok": true}'
    assert cap["req"].full_url == "https://api.openai.com/v1/chat/completions"
    body = cap["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    # V2.0 М5: потолок вывода — от reasoning_effort (дефолт low → 6000),
    # а не константа 8000/16000: короткий режим не тянет лишнюю генерацию.
    assert body["max_tokens"] == cl.OUTPUT_TOKEN_BUDGET["low"]


def test_openai_reasoning_model_no_temperature(monkeypatch):
    cap: dict = {}
    _mock_urlopen(monkeypatch, cap, response=_chat_response())
    client = cl.create_client(_cfg("openai", model_name="o3-mini"))
    client.generate_coach_report("p", {})
    body = cap["body"]
    assert "temperature" not in body
    assert "max_tokens" not in body
    # V2.0 М5: reasoning-модели получают бюджет от effort, не жёсткие 16k.
    assert body["max_completion_tokens"] == cl.OUTPUT_TOKEN_BUDGET["low"]


def test_openai_vision_image(monkeypatch):
    cap: dict = {}
    _mock_urlopen(monkeypatch, cap, response=_chat_response())
    client = cl.create_client(_cfg("openai", model_name="gpt-6-astra"))
    client.generate_coach_report("p", {}, image_bytes=b"PNG")
    content = cap["body"]["messages"][0]["content"]
    assert isinstance(content, list)
    b64 = base64.b64encode(b"PNG").decode()
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{b64}"},
    }


# --- anthropic -------------------------------------------------------------


def test_anthropic_payload_headers_thinking(monkeypatch):
    cap: dict = {}
    resp = {"content": [{"type": "text", "text": '{"ok":1}'}]}
    _mock_urlopen(monkeypatch, cap, response=resp)
    client = cl.create_client(_cfg("anthropic", model_name="claude-3-7-sonnet"))
    out = client.generate_coach_report("p", {})
    assert out == '{"ok":1}'
    assert cap["req"].full_url == "https://api.anthropic.com/v1/messages"
    assert cap["req"].headers.get("X-api-key") == "k-123"
    assert cap["req"].headers.get("Anthropic-version") == "2023-06-01"
    body = cap["body"]
    assert body["max_tokens"] == 8000 + min(2048, 8000)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert body["messages"][0]["content"][-1]["type"] == "text"


def test_anthropic_vision_base64_source(monkeypatch):
    cap: dict = {}
    resp = {"content": [{"type": "text", "text": "{}"}]}
    _mock_urlopen(monkeypatch, cap, response=resp)
    client = cl.create_client(_cfg("anthropic", enable_thinking=False))
    client.generate_coach_report("p", {}, image_bytes=b"IMG")
    block = cap["body"]["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert block["source"]["data"] == base64.b64encode(b"IMG").decode()


# --- HTTP errors -----------------------------------------------------------


@pytest.mark.parametrize("code", [401, 429])
def test_llm_http_errors_carry_body(monkeypatch, code):
    err = _FakeHTTPError(code, b'{"error": {"message": "quota exceeded"}}')
    _mock_urlopen(monkeypatch, {}, response=None, error=err)
    for provider in ("deepseek", "openai", "anthropic"):
        client = cl.create_client(_cfg(provider))
        with pytest.raises(cl.LLMError) as ei:
            client.generate_coach_report("p", {})
        assert str(code) in str(ei.value)
        assert "quota exceeded" in str(ei.value)


def test_llm_urlerror(monkeypatch):
    def fake(req, timeout=None):
        raise urllib.error.URLError("conn refused")

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    client = cl.create_client(_cfg("deepseek"))
    with pytest.raises(cl.LLMError, match="conn refused"):
        client.generate_coach_report("p", {})


def test_test_connection_ok(monkeypatch):
    _mock_urlopen(monkeypatch, {}, response=_chat_response())
    result = cl.test_connection(_cfg("deepseek"))
    assert result["ok"] is True
    assert result["latency_ms"] >= 0


def test_test_connection_bad_provider():
    result = cl.test_connection(_cfg("nope"))
    assert result["ok"] is False
    assert "unknown provider" in result["detail"]


# --- coach.analyze_match ---------------------------------------------------


def test_coach_valid_json(monkeypatch):
    stub = _StubClient(json.dumps(VALID_REPORT))
    report = coach_mod.analyze_match(_normalized_match(), 111, stub)
    assert isinstance(report, CoachReport)
    assert report.performance_grade == "A"
    assert report.coach_conclusion == ["раз", "два", "три"]
    assert report.benchmarks.souls_at_10 == 0.5
    # промпт содержит компактные данные
    call = stub.calls[0]
    assert call["match_data"]["kda"] == "14/3/9"
    assert call["match_data"]["my_hero"] == "infernus"
    assert len(call["match_data"]["networth_curve_20pts"]) <= 21
    assert "JSON" in call["prompt"] or "json" in call["prompt"].lower()


def test_coach_json_fence_stripped():
    stub = _StubClient("```json\n" + json.dumps(VALID_REPORT) + "\n```")
    report = coach_mod.analyze_match(_normalized_match(), 111, stub)
    assert report.performance_grade == "A"


def test_coach_invalid_schema_raises_llm_error():
    bad = dict(VALID_REPORT)
    bad["performance_grade"] = "ZZZ"
    stub = _StubClient(json.dumps(bad))
    with pytest.raises(cl.LLMError, match="invalid schema"):
        coach_mod.analyze_match(_normalized_match(), 111, stub)


def test_coach_grade_out_of_vocab_is_clamped_not_failed():
    """LLM вернул 'D' вне алфавита S+/S/A/B/C — клампим в 'C', отчёт живой."""
    bad = dict(VALID_REPORT)
    bad["performance_grade"] = "D"
    stub = _StubClient(json.dumps(bad))
    report = coach_mod.analyze_match(_normalized_match(), 111, stub)
    assert report.performance_grade == "C"


def test_coach_invalid_json_raises_llm_error():
    stub = _StubClient("это не JSON вообще")
    with pytest.raises(cl.LLMError, match="invalid JSON"):
        coach_mod.analyze_match(_normalized_match(), 111, stub)


def test_coach_fallback_on_llm_error():
    stub = _StubClient(cl.LLMError("network down"))
    report = coach_mod.analyze_match(_normalized_match(), 111, stub)
    assert isinstance(report, CoachReport)
    assert report.performance_grade in ("S+", "S", "A", "B", "C")
    # реальные цифры, не заглушки
    joined = " ".join(report.coach_conclusion)
    assert "14/3/9" in joined
    assert report.benchmarks.fight_participation > 0
    assert report.key_mistake.time != "00:00"


def test_coach_passes_image_bytes():
    stub = _StubClient(json.dumps(VALID_REPORT))
    coach_mod.analyze_match(_normalized_match(), 111, stub, image_bytes=b"PNG")
    assert stub.calls[0]["image_bytes"] == b"PNG"


def test_coach_accepts_dict_match():
    stub = _StubClient(json.dumps(VALID_REPORT))
    report = coach_mod.analyze_match({"kda": "14/3/9"}, None, stub)
    assert isinstance(report, CoachReport)
    assert stub.calls[0]["match_data"] == {"kda": "14/3/9"}


def test_coach_system_prompt_shape():
    assert "performance_grade" in coach_mod.COACH_SYSTEM_PROMPT
    assert "JSON" in coach_mod.COACH_SYSTEM_PROMPT
    # Фикс «таймаут 200с»: жёсткое требование лаконичности в промпте
    assert "ЛАКОНИЧНОСТЬ" in coach_mod.COACH_SYSTEM_PROMPT
    assert "300–700 слов" in coach_mod.COACH_SYSTEM_PROMPT


def test_heuristic_report_from_normalized():
    report = coach_mod.heuristic_report(_normalized_match(), 111)
    assert isinstance(report, CoachReport)
    assert report.benchmarks.souls_at_10 > 0  # 11000 душ к 10-й минуте


def test_client_default_headers_applied(monkeypatch):
    cap = {}
    _mock_urlopen(monkeypatch, cap, response=_chat_response())
    c = cl.create_client(_cfg("openai"))
    c.generate_coach_report("p", {})
    req = cap["req"]
    assert req.headers.get("User-agent") == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.6"
    assert req.headers.get("Connection") == "close"


def test_post_json_retry_on_remote_disconnected(monkeypatch):
    import http.client

    attempts = [0]

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake(req, timeout=None):
        attempts[0] += 1
        if attempts[0] == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    res = cl._post_json("https://fake", {}, {}, max_retries=2, retry_delay=0.01)
    assert res == {"ok": True}
    assert attempts[0] == 2


def test_post_json_retry_on_502_error(monkeypatch):
    attempts = [0]

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake(req, timeout=None):
        attempts[0] += 1
        if attempts[0] == 1:
            raise _FakeHTTPError(502, b"Bad Gateway")
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    res = cl._post_json("https://fake", {}, {}, max_retries=2, retry_delay=0.01)
    assert res == {"ok": True}
    assert attempts[0] == 2


def test_post_json_no_retry_on_socket_timeout(monkeypatch):
    """Фикс «таймаут 200с»: таймаут генерации НЕ ретраим (иначе 120+120=240с
    и упор в лимит эндпоинта) — сразу LLMError, fallback на следующую модель."""
    attempts = [0]

    def fake(req, timeout=None):
        attempts[0] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    with pytest.raises(cl.LLMError, match="timed out"):
        cl._post_json("https://fake", {}, {}, max_retries=2, retry_delay=0.01)
    assert attempts[0] == 1


def test_is_timeout_error_classification():
    assert cl._is_timeout_error(TimeoutError("timed out")) is True
    assert cl._is_timeout_error(urllib.error.URLError(TimeoutError("timed out"))) is True
    assert cl._is_timeout_error(urllib.error.URLError("conn refused")) is False
    assert cl._is_timeout_error(ConnectionResetError("reset")) is False


def test_post_json_exhaust_retries_raises_llm_error(monkeypatch):
    import http.client

    def fake(req, timeout=None):
        raise http.client.RemoteDisconnected("Drop")

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    with pytest.raises(cl.LLMError, match="Drop"):
        cl._post_json("https://fake", {}, {}, max_retries=2, retry_delay=0.01)


def test_stream_chat_completions_retry_and_fallback(monkeypatch):
    import http.client

    attempts = [0]

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return b'{"choices": [{"message": {"content": "fallback-ok"}}]}'

    def fake(req, timeout=None):
        attempts[0] += 1
        if "text/event-stream" in req.headers.get("Accept", ""):
            raise http.client.RemoteDisconnected("SSE drop")
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake)
    out = cl._stream_chat_completions(
        "https://fake", {}, {"messages": []}, timeout=10,
        on_chunk=None, max_retries=1, retry_delay=0.01
    )
    assert out == "fallback-ok"


def test_russian_localization_and_term_cleansing():
    from deadlockforge.ai.deadlock_knowledge import localize_text, localize_report_dict

    # Test phrase from user's screenshot
    src_text = (
        "Смерть от Vindicta при агрессивном пуше без контроля дистанции и "
        "под Flight + Assassinate от Vindicta в активной фазе Midboss. "
        "Против Vindicta и Lash запрещено открываться без Knockdown, "
        "вынуждая Vindicta опускаться, либо забрать Midboss через Rejuvenator под T2 Walker."
    )
    localized = localize_text(src_text)
    assert "Vindicta" not in localized
    assert "Lash" not in localized
    assert "Flight" not in localized
    assert "Assassinate" not in localized
    assert "Midboss" not in localized
    assert "Rejuvenator" not in localized
    assert "Knockdown" not in localized
    assert "Walker" not in localized

    assert "Виндикты" in localized
    assert "Хлыста" in localized
    assert "Полёт + Ликвидацию" in localized
    assert "Мидбосса" in localized
    assert "Омолодитель" in localized
    assert "Нокдаун" in localized
    assert "Шагоход T2" in localized

    # Test validate_report with English terms
    raw_json = json.dumps({
        "performance_grade": "A",
        "key_mistake": {
            "time": "18:07",
            "location": "район T2 Walker",
            "event": "Смерть от Vindicta",
            "why_fatal": "Подставился под Flight + Assassinate от Vindicta в фазе Midboss",
            "correct_play": "Против Vindicta и Lash собирать Knockdown и забирать Rejuvenator"
        },
        "coach_conclusion": [
            "Фарм 5500 душ",
            "Против Haze и Wraith покупать Metal Skin и Return Fire",
            "Не пушить без Rejuvenator"
        ],
        "benchmarks": {"souls_at_10": 1.1},
        "item_corrections": []
    })
    report = coach_mod._validate_report(raw_json)
    assert "Vindicta" not in report.key_mistake.event
    assert "Виндикты" in report.key_mistake.event
    assert "Шагоход T2" in report.key_mistake.location
    assert "Нокдаун" in report.key_mistake.correct_play
    assert "Омолодитель" in report.key_mistake.correct_play
    assert "Хейз" in report.coach_conclusion[1]
    assert "Металлическая кожа" in report.coach_conclusion[1]


def test_compute_match_benchmarks():
    match = _normalized_match()
    me = match.players[0]  # Infernus, 17000 souls at t=540s <= 600s, kills 14, assists 9, team kills 14
    bm = coach_mod.compute_match_benchmarks(match, me)
    assert bm["actual_souls"] == 17000
    assert bm["target_souls"] == 5000  # Infernus is carry
    assert bm["souls_at_10"] == 3.4  # 17000 / 5000
    assert bm["fight_participation"] > 0
    assert bm["lane_efficiency"] >= 1.0


def test_lane_opponent_carries_hero_slug_for_portrait():
    """Соперник по линии отдаёт и русское имя, и слаг.

    По русскому имени портрет не находится (файл — assets/heroes/<slug>.webp),
    поэтому Deep Match рисует букву вместо картинки.
    """
    match = _normalized_match()
    me = match.players[0]
    lane = coach_mod._lane_opponent(match, me)
    assert lane is not None
    assert lane["hero_id"] == "abrams"
    assert lane["hero"] == "Абрамс"


# --- V2.0: три глубины разбора (ТЗ §3.1) -----------------------------------


def test_coach_prompt_depth_short():
    p = coach_mod.get_coach_prompt("short")
    assert "«КОРОТКИЙ»" in p
    # короткий режим требует ровно 3 пункта заключения
    assert "РОВНО 3 пункта" in p


def test_coach_prompt_depth_medium_is_default():
    assert coach_mod.get_coach_prompt() == coach_mod.get_coach_prompt("medium")
    assert "«СРЕДНИЙ»" in coach_mod.get_coach_prompt()
    assert "purchase_timings" in coach_mod.get_coach_prompt("medium")


def test_coach_prompt_depth_deep():
    p = coach_mod.get_coach_prompt("deep")
    assert "«МАКСИМАЛЬНЫЙ»" in p
    assert "death_breakdown" in p
    assert "lane_comparison" in p


def test_coach_prompt_depth_case_insensitive():
    assert "«СРЕДНИЙ»" in coach_mod.get_coach_prompt("MEDIUM")
    assert "«КОРОТКИЙ»" in coach_mod.get_coach_prompt(" Short ")


def test_coach_prompt_depth_invalid_raises():
    with pytest.raises(ValueError, match="unknown depth"):
        coach_mod.get_coach_prompt("ultra")
    with pytest.raises(ValueError, match="unknown depth"):
        coach_mod.analyze_match(_normalized_match(), 111, _StubClient("x"), depth="bogus")


@pytest.mark.parametrize("depth", ["short", "medium", "deep"])
def test_coach_analyze_depth_returns_valid_report(depth):
    """Все три глубины дают валидный CoachReport (мок клиента)."""
    stub = _StubClient(json.dumps(VALID_REPORT))
    report = coach_mod.analyze_match(_normalized_match(), 111, stub, depth=depth)
    assert isinstance(report, CoachReport)
    assert report.performance_grade == "A"
    assert report.coach_conclusion == ["раз", "два", "три"]
    # промпт соответствует выбранной глубине
    assert "«КОРОТКИЙ»" in stub.calls[0]["prompt"] or depth != "short"
    assert "death_breakdown" in stub.calls[0]["prompt"] or depth != "deep"
    # данные матча всё ещё компактные
    assert stub.calls[0]["match_data"]["kda"] == "14/3/9"


def test_coach_analyze_depth_default_medium():
    stub = _StubClient(json.dumps(VALID_REPORT))
    coach_mod.analyze_match(_normalized_match(), 111, stub)
    assert "«СРЕДНИЙ»" in stub.calls[0]["prompt"]


def test_coach_analyze_stream_depth_passed():
    stub = _StubClient(json.dumps(VALID_REPORT))
    coach_mod.analyze_match_stream(_normalized_match(), 111, stub, depth="deep")
    assert "«МАКСИМАЛЬНЫЙ»" in stub.calls[0]["prompt"]


def test_coach_dataset_includes_lane_opponent():
    """В компактных данных матча есть эвристика соперника по линии."""
    stub = _StubClient(json.dumps(VALID_REPORT))
    coach_mod.analyze_match(_normalized_match(), 111, stub, depth="deep")
    data = stub.calls[0]["match_data"]
    assert isinstance(data.get("lane_opponent"), dict)
    # игрока 111 (infernus) убивали из team 1 (abrams) — он и есть соперник линии
    assert data["lane_opponent"]["hero"] == "Абрамс"


def test_coach_prompt_player_context_and_override():
    """Персональный контекст и правила пользователя попадают в промпт."""
    stub = _StubClient(json.dumps(VALID_REPORT))
    ctx = {
        "trend": {"latest": {"match_id": "m1", "grade_label": "A", "kda": "14/3/9",
                             "deaths": 3, "souls_at_10": 1.1, "win": True}},
        "weaknesses": {"total_reviews": 1, "death_timing_buckets": {
            "early_0_10": 0, "mid_10_20": 1, "late_20_plus": 0}},
    }
    coach_mod.analyze_match(
        _normalized_match(), 111, stub,
        player_context=ctx, system_prompt_override="Всегда покупай Нокдаун до 15:00.",
    )
    prompt = stub.calls[0]["prompt"]
    assert "ИСТОРИЯ МАТЧЕЙ ИГРОКА" in prompt
    assert "СЛАБОСТИ ИГРОКА" in prompt
    assert "Нокдаун" in prompt



