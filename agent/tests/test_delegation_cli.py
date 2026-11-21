from pathlib import Path

import pytest

import voca.__main__


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
    monkeypatch.setattr("sys.argv", ["voca", "bench", "delegate", *args])
    with pytest.raises(SystemExit) as stopped:
        voca.__main__.main()
    return stopped.value.code


def test_bench_delegate_needs_hermes_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tasks.sqlite3"
    monkeypatch.setenv("VOCA_DELEGATION__PATH", str(path))
    assert run_cli(monkeypatch, "сделай") == 2
    assert "VOCA_HERMES_URL" in capsys.readouterr().err
    assert not path.exists()


def test_bench_delegate_bad_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tasks.sqlite3"
    monkeypatch.setenv("VOCA_DELEGATION__PATH", str(path))
    monkeypatch.setenv("VOCA_HERMES_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("VOCA_HERMES_KEY", "k")
    for args in ((), ("--approve", "once"), ("--recover", "сделай")):
        assert run_cli(monkeypatch, *args) == 2
        assert "нужен текст, --recover или --approve с --key" in capsys.readouterr().err
    assert not path.exists()


def test_bench_delegate_bad_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[delegation]\nunknown = 1\n", encoding="utf-8")
    monkeypatch.setenv("VOCA_CONFIG", str(config))
    assert run_cli(monkeypatch, "сделай") == 2
    err = capsys.readouterr().err
    assert "voca bench: конфиг не прочитан (" in err
    assert str(config) in err
