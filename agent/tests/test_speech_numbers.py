from pathlib import Path

import pytest

from voca.speech.numbers import cardinal, normalize_numbers, ordinal

CORPUS = Path(__file__).resolve().parents[2] / "bench" / "corpus"
NORM_FLOOR = 28


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Конференция пройдёт 14 марта.", "Конференция пройдёт четырнадцатого марта."),
        (
            "Встреча на пятницу, 21 ноября, в 16:45.",
            "Встреча на пятницу, двадцать первое ноября, в шестнадцать сорок пять.",
        ),
        ("с 22 декабря по 9 марта", "с двадцать второго декабря по девятому марта"),
        ("перенесли на 3 мая", "перенесли на третье мая"),
    ],
)
def test_date(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("В 2026 году", "В две тысячи двадцать шестом году"),
        ("Январь 2027 года", "Январь две тысячи двадцать седьмого года"),
        ("к 2030 году", "к две тысячи тридцатому году"),
        ("1147 год", "тысяча сто сорок седьмой год"),
    ],
)
def test_year(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Работаем до 18:00.", "Работаем до восемнадцати ноль-ноль."),
        ("Начало в 10:30.", "Начало в десять тридцать."),
        ("Поезд в 7:05.", "Поезд в семь ноль пять."),
    ],
)
def test_time(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Позвоните 8 800 555-35-35.",
            "Позвоните восемь восемьсот пятьсот пятьдесят пять тридцать пять тридцать пять.",
        ),
        (
            "+7 (912) 305-00-11",
            "плюс семь девятьсот двенадцать триста пять ноль ноль одиннадцать",
        ),
    ],
)
def test_phone(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3,5 миллиона", "три с половиной миллиона"),
        (
            "8 848,86 метра",
            "восемь тысяч восемьсот сорок восемь целых восемьдесят шесть сотых метра",
        ),
        ("около 5,6 миллиона", "около пяти целых шести десятых миллиона"),
        ("1,1", "одна целая одна десятая"),
    ],
)
def test_decimal(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 %", "двенадцать процентов"),
        ("15%", "пятнадцать процентов"),
        ("22 %", "двадцать два процента"),
        ("до 2 %", "до двух процентов"),
        ("$600", "шестьсот долларов"),
        ("$4 000", "четыре тысячи долларов"),
        ("21 ₽", "двадцать один рубль"),
        ("3 €", "три евро"),
        ("1 245 рублей", "тысяча двести сорок пять рублей"),
    ],
)
def test_percent_and_currency(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("до минус 23 градусов", "до минус двадцати трёх градусов"),
        ("к 5", "к пяти"),
        ("-5 градусов", "минус пять градусов"),
        ("было 312 человек", "было триста двенадцать человек"),
    ],
)
def test_preposition_case(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10–15 %", "десять — пятнадцать процентов"),
        ("от 400–600 рублей", "от четырёхсот — шестисот рублей"),
        ("10-12 часов", "десять — двенадцать часов"),
    ],
)
def test_range(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("UTC+10", "UTC плюс десять"),
        (
            "3 × 280 + 190",
            "три умножить на двести восемьдесят плюс сто девяносто",
        ),
        ("15% = $600", "пятнадцать процентов равно шестьсот долларов"),
        ("(≈ 720 по прямой)", "(примерно семьсот двадцать по прямой)"),
        ("2*3", "два умножить на три"),
    ],
)
def test_symbols_between_numbers(text: str, expected: str) -> None:
    assert normalize_numbers(text) == expected


def test_thousand_prefix_dropped() -> None:
    assert cardinal(1245) == "тысяча двести сорок пять"
    assert ordinal(1147, "n", "m") == "тысяча сто сорок седьмой"


def squash(text: str) -> str:
    return " ".join(text.casefold().split())


def test_corpus_floor() -> None:
    source = (CORPUS / "tts_ru.txt").read_text(encoding="utf-8").splitlines()
    expected = (CORPUS / "tts_ru_norm.txt").read_text(encoding="utf-8").splitlines()
    matches = sum(
        squash(normalize_numbers(line)) == squash(want)
        for line, want in zip(source, expected, strict=True)
    )
    assert matches >= NORM_FLOOR
