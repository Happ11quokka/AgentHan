"""배경 제거 모델(rembg) 미리 읽기 (tools/image_studio.py)."""

import sys
import threading
import types

import numpy as np

from agenthan.tools import image_studio


def fake_rembg(monkeypatch):
    """rembg 대신: 세션을 몇 번 만드는지 센다(진짜 import는 numba 컴파일로 서버에서 34.5초)."""
    made = []

    def new_session(name):
        made.append(name)
        return object()

    def remove(rgb, *, session, only_mask):
        assert only_mask and session is not None
        return np.full(rgb.shape[:2], 255, np.uint8)

    module = types.SimpleNamespace(new_session=new_session, remove=remove)
    monkeypatch.setitem(sys.modules, "rembg", module)
    monkeypatch.setattr(image_studio, "_rembg_session", None)
    monkeypatch.setenv(image_studio.MODEL_ENV, "rembg")
    return made


def test_warm_loads_the_model_once_and_the_first_cutout_reuses_it(monkeypatch):
    made = fake_rembg(monkeypatch)
    assert image_studio.warm() is True
    assert made == ["u2net"]
    image_studio.rembg_mask(np.zeros((40, 40, 3), np.uint8))
    assert made == ["u2net"]  # 첫 사진이 모델을 다시 읽지 않는다


def test_warm_does_nothing_without_rembg(monkeypatch):
    made = fake_rembg(monkeypatch)
    monkeypatch.delenv(image_studio.MODEL_ENV)
    assert image_studio.warm() is False
    assert made == []


def test_warm_in_background_does_not_block_and_swallows_errors(monkeypatch, caplog):
    fake_rembg(monkeypatch)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        raise RuntimeError("모델 파일 없음")

    monkeypatch.setattr(image_studio, "_session", slow)
    thread = image_studio.warm_in_background()  # 막히지 않고 바로 돌아온다
    assert started.wait(5)
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert "cutout model warm failed" in caplog.text
