"""V2.0 М4: автофолбэк модели при «остывании» (429/cooling).

Пользователь: «GLM 5.3 остывает, нужно иметь возможность сменить модель».
analyze_match_with_fallback пробует выбранную модель, затем цепочку
FALLBACK_MODELS. Тест: первая модель кидает LLMError (cooling down), вторая
отвечает валидным CoachReport — проверяем переключение.
"""

from __future__ import annotations

import json
import sys
import sqlite3
import unittest.mock as um
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.engine import match_loader as ml
from deadlockforge.models import AIConfig
from deadlockforge.ai import coach as coach_mod
from deadlockforge.ai.client import LLMError

VALID_REPORT = json.dumps({
    "performance_grade": "A",
    "key_mistake": {"time": "12:00", "location": "мид", "event": "файт",
                    "why_fatal": "потеряли урну", "correct_play": "не драться"},
    "coach_conclusion": ["пункт 1 — темп фарма хороший",
                         "пункт 2 — аудит билда",
                         "пункт 3 — упражнение на позиционку"],
    "benchmarks": {"souls_at_10": 1.1, "lane_efficiency": 0.9,
                   "fight_participation": 0.5},
    "item_corrections": [],
})


class _FakeClient:
    def __init__(self, fail: bool):
        self.fail = fail

    def generate_coach_report(self, prompt, match_data, image_bytes=None):
        if self.fail:
            raise LLMError("429 too many requests, cooling down")
        return VALID_REPORT


def _real_match():
    con = sqlite3.connect(
        str(Path(__file__).resolve().parents[1] / "data" / "deadlockforge.db"))
    row = con.execute("SELECT payload FROM match_raw LIMIT 1").fetchone()
    con.close()
    if row is None:
        pytest.skip("match_raw пуст — нечего разбирать")
    return ml.normalize_match(json.loads(row[0]), ml.load_hero_map())


def test_fallback_switches_to_working_model():
    """Первая модель «остыла» (429) → цепочка переходит на glm-5.3-flash."""
    import deadlockforge.ai.client as client_mod

    match = _real_match()
    calls: list[str] = []

    def fake_factory(cfg):
        calls.append(cfg.model_name)
        return _FakeClient(fail=cfg.model_name != "glm-5.3-flash")

    cfg = AIConfig(provider="openai", model_name="kimi-k3", api_key="sk-x",
                   base_url="http://127.0.0.1:8089/v1")
    with um.patch.object(client_mod, "create_client", fake_factory):
        report, used = coach_mod.analyze_match_with_fallback(
            match, match.players[0].account_id, cfg, depth="short")

    assert used == "glm-5.3-flash", calls
    assert calls[0] == "kimi-k3"
    assert calls[1] == "glm-5.3-flash"
    assert report.performance_grade == "A"


def test_fallback_first_model_ok_no_retry():
    """Если выбранная модель работает — цепочка делает одну попытку."""
    import deadlockforge.ai.client as client_mod

    match = _real_match()
    calls: list[str] = []

    def fake_factory(cfg):
        calls.append(cfg.model_name)
        return _FakeClient(fail=False)

    cfg = AIConfig(provider="openai", model_name="glm-5.3-flash", api_key="sk-x",
                   base_url="http://127.0.0.1:8089/v1")
    with um.patch.object(client_mod, "create_client", fake_factory):
        report, used = coach_mod.analyze_match_with_fallback(
            match, match.players[0].account_id, cfg, depth="short")

    assert used == "glm-5.3-flash"
    assert len(calls) == 1


class _BrokenStreamClient:
    """Стрим всегда падает; без стрим — валидный отчёт (фолбэк-путь)."""

    def __init__(self):
        self.request_timeout = 180.0
        self.stream_calls = 0
        self.full_calls = 0

    def generate_coach_report_stream(self, prompt, match_data,
                                     image_bytes=None, on_chunk=None):
        self.stream_calls += 1
        raise LLMError("stream broken")

    def generate_coach_report(self, prompt, match_data, image_bytes=None):
        self.full_calls += 1
        return VALID_REPORT


def test_stream_fallback_skipped_after_deadline():
    """Фолбэк в не-стриминг после истечения дедлайна НЕ делается (двойная
    оплата — только жжёт квоту, в окно уже не уложиться)."""
    import time

    match = _real_match()
    client = _BrokenStreamClient()
    with pytest.raises(LLMError):
        coach_mod.analyze_match_stream(
            match, match.players[0].account_id, client,
            depth="short", deadline=time.monotonic() - 1)  # окно уже истекло
    assert client.stream_calls == 1
    assert client.full_calls == 0


def test_stream_fallback_socket_clamped_to_remaining():
    """Фолбэк получает сокет-таймаут = остатку окна, а не полный 180с."""
    import time

    match = _real_match()
    client = _BrokenStreamClient()
    report = coach_mod.analyze_match_stream(
        match, match.players[0].account_id, client,
        depth="short", deadline=time.monotonic() + 30)
    assert report.performance_grade == "A"
    assert client.full_calls == 1
    assert client.request_timeout <= 30.1  # урезан с 180 до остатка окна
    assert client.request_timeout >= 1.0