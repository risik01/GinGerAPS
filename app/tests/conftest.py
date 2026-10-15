import pytest

from camaps_diary.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "diario.db")
    yield s
    s.close()
