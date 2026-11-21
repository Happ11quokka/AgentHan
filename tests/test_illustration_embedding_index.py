"""대표 그림 임베딩 인덱스 만들기: 카탈로그 전체 id의 벡터를 쓰고, 바뀐 글만 다시 임베딩한다.

네트워크를 쓰지 않는다. 가짜 임베딩이 부른 글을 세고, 진짜 CLOVA 함수는 키 없이만 부른다.
"""

import json

import pytest

from agenthan.core import embedding
from agenthan.illustration import build_index, embedding_index, library


def row(key, name, aliases=()):
    return {
        "id": key,
        "name": name,
        "aliases": list(aliases),
        "keywords": [],
        "category": "cake",
        "recipe": {},
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """임시 카탈로그 폴더와 임시 인덱스 파일. write(rows)로 카탈로그를 바꾼다."""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    monkeypatch.setattr(library, "CATALOG_DIR", catalog)
    monkeypatch.setattr(embedding_index, "INDEX_PATH", tmp_path / "index.json")
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)

    def no_network(*_args, **_kwargs):
        pytest.fail("인덱스 테스트는 네트워크를 쓰지 않는다")

    monkeypatch.setattr(embedding.urllib.request, "urlopen", no_network)

    def write(rows):
        (catalog / "cakes.json").write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
        library.load_catalog.cache_clear()

    embedding_index.stored.cache_clear()
    yield write
    library.load_catalog.cache_clear()
    embedding_index.stored.cache_clear()


class CountingEmbed:
    def __init__(self, fail_after=None):
        self.texts = []
        self.fail_after = fail_after

    def __call__(self, text):
        if self.fail_after is not None and len(self.texts) >= self.fail_after:
            raise embedding.ClovaEmbeddingError("CLOVA embedding HTTP request failed")
        self.texts.append(text)
        vector = [0.0] * 1024
        vector[len(self.texts)] = 1.0
        return vector


def test_build_writes_a_vector_for_every_catalog_id(workspace):
    workspace([row("cake_a", "메도빅", ["메도빅 허니 케이크"]), row("cake_b", "나폴레옹")])
    embed = CountingEmbed()
    assert embedding_index.build_index(embed) == ["cake_a", "cake_b"]
    assert embed.texts == ["메도빅\n메도빅 허니 케이크", "나폴레옹"]  # 이름과 별칭을 한 글로
    assert set(embedding_index.load_index()) == {"cake_a", "cake_b"}


def test_a_rebuild_embeds_only_new_or_changed_texts(workspace):
    workspace([row("cake_a", "메도빅"), row("cake_b", "나폴레옹"), row("cake_gone", "없어질 것")])
    embedding_index.build_index(CountingEmbed())
    before = embedding_index.INDEX_PATH.read_text("utf-8")

    unchanged = CountingEmbed()
    workspace([row("cake_a", "메도빅"), row("cake_b", "나폴레옹"), row("cake_gone", "없어질 것")])
    assert embedding_index.build_index(unchanged) == []
    assert unchanged.texts == []
    assert embedding_index.INDEX_PATH.read_text("utf-8") == before  # 그대로면 파일도 그대로

    workspace(
        [
            row("cake_a", "메도빅"),
            row("cake_b", "나폴레옹", ["나폴레옹 케이크"]),
            row("cake_c", "프라하"),
        ]
    )
    changed = CountingEmbed()
    assert embedding_index.build_index(changed) == ["cake_b", "cake_c"]
    assert changed.texts == ["나폴레옹\n나폴레옹 케이크", "프라하"]
    assert set(embedding_index.stored()) == {"cake_a", "cake_b", "cake_c"}  # 빠진 그림은 지운다


def test_a_failed_build_keeps_what_it_embedded_and_resumes(workspace):
    workspace([row("cake_a", "메도빅"), row("cake_b", "나폴레옹"), row("cake_c", "프라하")])
    with pytest.raises(embedding.ClovaEmbeddingError):
        embedding_index.build_index(CountingEmbed(fail_after=1))
    assert set(embedding_index.load_index()) == {"cake_a"}
    rest = CountingEmbed()
    assert embedding_index.build_index(rest) == ["cake_b", "cake_c"]


def test_the_command_prints_the_embedded_ids(workspace, monkeypatch, capsys):
    workspace([row("cake_a", "메도빅"), row("cake_b", "나폴레옹")])
    monkeypatch.setattr(embedding_index, "embed_text", CountingEmbed())
    assert build_index.main() == 0
    out = capsys.readouterr().out
    assert "새로 임베딩 2개" in out and "임베딩: cake_a" in out and "임베딩: cake_b" in out


def test_without_a_key_the_command_fails_cleanly_unless_nothing_changed(workspace, capsys):
    workspace([row("cake_a", "메도빅")])
    assert build_index.main() == 1  # 진짜 CLOVA 함수, 키 없음 → 네트워크 없이 실패
    assert "CLOVASTUDIO_API_KEY" in capsys.readouterr().err
    embedding_index.build_index(CountingEmbed())
    assert build_index.main() == 0  # 새로 임베딩할 글이 없으면 키가 필요 없다
    assert "새로 임베딩 0개" in capsys.readouterr().out
