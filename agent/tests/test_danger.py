from voca.conversation.descriptions import DESCRIPTIONS
from voca.delegation.danger import RULES, Danger, level

MEDIUM = {
    "stop/restart system service",
    "force stop service",
    "docker compose restart/stop/kill/down",
    "docker restart/stop/kill",
    "force kill processes",
    "kill processes by regex",
    "kill process via pgrep/pidof expansion",
    "kill process via backtick pgrep/pidof expansion",
    "package manager uninstall",
    "overwrite project env/config via tee",
    "overwrite project env/config via redirection",
    "overwrite project env/config file",
    "script execution via -e/-c flag",
    "script execution via heredoc",
    "shell execution via heredoc",
}


def test_rules_cover_descriptions() -> None:
    assert set(RULES) == set(DESCRIPTIONS)


def test_medium_rules() -> None:
    assert {key for key, value in RULES.items() if value == Danger.MEDIUM} == MEDIUM


def test_rm_r_is_high() -> None:
    assert level("delete in root path") == Danger.HIGH
    assert level("recursive delete of home directory") == Danger.HIGH


def test_hermes_gateway_is_high() -> None:
    assert level("stop/restart hermes gateway") == Danger.HIGH
    assert level("kill hermes/gateway process") == Danger.HIGH


def test_parenthetical_suffix() -> None:
    assert level("package manager uninstall (pip)") == Danger.MEDIUM


def test_unknown_is_high() -> None:
    for description in (
        None,
        "",
        "something new",
        "arbitrary program execution via python",
    ):
        assert level(description) == Danger.HIGH
