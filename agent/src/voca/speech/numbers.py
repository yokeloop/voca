import re

from num2words import num2words

NUM = r"\d{1,3}(?:[   ]\d{3})+(?![\w])|\d+(?![\w])"
NUMBER = re.compile(rf"(?<![\w])(?:{NUM})")
THOUSAND_ONE = re.compile(r"^(?:одна|одной|одну) (?=тысяч)")
WORD_BEFORE = re.compile(r"(\w+)\s*$")
APPOSITION = re.compile(r"(?:^|\s)(?:на|в)\s+\w+,\s*$", re.IGNORECASE)
GENITIVE = frozenset({"до", "от", "после", "около", "с", "из", "без", "для", "у"})
DATIVE = frozenset({"к", "по"})
PREPOSITIONAL = frozenset({"о", "об", "при"})
MONTHS = (
    "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
)
PHONE = re.compile(
    r"(?<![\w+])(?:(?:\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}"
    r"|\d{3}[\s-]\d{3}[\s-]\d{2}[\s-]\d{2})(?![\w])"
)
MINUS_START = re.compile(r"(?<![\w])[-−](?=\d)")
MINUS = re.compile(r"\s*−\s*")
PLUS = re.compile(r"(?<=\w)\s*\+\s*(?=\d)")
TIMES = re.compile(r"\s*×\s*|(?<=\d)\s*\*\s*(?=\d)")
EQUALS = re.compile(r"\s*=\s*")
APPROX = re.compile(r"≈\s*")
DATE = re.compile(rf"(?<![\w])(\d{{1,2}})\s({MONTHS})(?![\w])")
YEAR = re.compile(rf"(?<![\w])({NUM})\s(год|года|году|годом|годах)(?![\w])")
TIME = re.compile(r"(?<![\w:])(\d{1,2}):(\d{2})(?![\w:])")
DECIMAL = re.compile(rf"(?<![\w,])({NUM}),(\d+)(?![\w])")
DECIMAL_SUFFIX_UNIT = re.compile(rf"(?<![\w,])({NUM}),(\d+)\s?([%$€₽])")
DECIMAL_PREFIX_UNIT = re.compile(
    rf"([$€])({NUM}),(\d+)(?![\w])"
    r"(?:\s(?:(млн|млрд|тыс)\.?(?![\w])|(миллион\w*|миллиард\w*|тысяч\w*)))?"
)
SUFFIX_UNIT = re.compile(rf"(?<![\w])(?:({NUM})[–-])?({NUM})\s?([%$€₽])")
PREFIX_UNIT = re.compile(rf"([$€])({NUM})(?:[–-]({NUM}))?")
RANGE = re.compile(rf"(?<![\w])({NUM})[–-]({NUM})")
UNITS: dict[str, dict[str, tuple[str, ...]]] = {
    "%": {
        "n": ("процент", "процента", "процентов"),
        "g": ("процента", "процентов"),
        "d": ("проценту", "процентам"),
        "p": ("проценте", "процентах"),
    },
    "$": {
        "n": ("доллар", "доллара", "долларов"),
        "g": ("доллара", "долларов"),
        "d": ("доллару", "долларам"),
        "p": ("долларе", "долларах"),
    },
    "€": {
        "n": ("евро", "евро", "евро"),
        "g": ("евро", "евро"),
        "d": ("евро", "евро"),
        "p": ("евро", "евро"),
    },
    "₽": {
        "n": ("рубль", "рубля", "рублей"),
        "g": ("рубля", "рублей"),
        "d": ("рублю", "рублям"),
        "p": ("рубле", "рублях"),
    },
}
SCALES = {
    "млн": ("миллион", "миллиона", "миллионов"),
    "млрд": ("миллиард", "миллиарда", "миллиардов"),
    "тыс": ("тысяча", "тысячи", "тысяч"),
}
UNIT_GENDER = {"%": "m", "$": "m", "€": "n", "₽": "m"}
FRACTIONS = (
    ("десятая", "десятых"),
    ("сотая", "сотых"),
    ("тысячная", "тысячных"),
)


def value(digits: str) -> int:
    return int(re.sub(r"\D", "", digits))


def cardinal(n: int, case: str = "n", gender: str = "m") -> str:
    words = num2words(n, lang="ru", to="cardinal", case=case, gender=gender)
    return THOUSAND_ONE.sub("", str(words))


def ordinal(n: int, case: str, gender: str) -> str:
    words = num2words(n, lang="ru", to="ordinal", case=case, gender=gender)
    return THOUSAND_ONE.sub("", str(words))


def word_before(text: str, start: int) -> tuple[str, int]:
    match = WORD_BEFORE.search(text[:start])
    if match is None:
        return "", start
    return match.group(1).lower(), match.start()


def case_before(text: str, start: int) -> str:
    word, position = word_before(text, start)
    if word == "минус":
        word, _ = word_before(text, position)
    if word in GENITIVE:
        return "g"
    if word in DATIVE:
        return "d"
    if word in PREPOSITIONAL:
        return "p"
    return "n"


def is_one(n: int) -> bool:
    return n % 10 == 1 and n % 100 != 11


def count_form(forms: tuple[str, ...], n: int) -> str:
    if is_one(n):
        return forms[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return forms[1]
    return forms[2]


def unit_form(symbol: str, n: int, case: str) -> str:
    forms = UNITS[symbol][case]
    if case == "n":
        return count_form(forms, n)
    return forms[0] if is_one(n) else forms[1]


def decimal_count(forms: tuple[str, ...], whole: int, fraction: str) -> str:
    if fraction == "5" and not is_one(whole):
        return count_form(forms, whole)
    return forms[1]


def phone(match: re.Match[str]) -> str:
    words = ["плюс"] if match.group(0).startswith("+") else []
    for group in re.findall(r"\d+", match.group(0)):
        rest = group.lstrip("0")
        words.extend(["ноль"] * (len(group) - len(rest)))
        if rest:
            words.append(cardinal(int(rest)))
    return " ".join(words)


def date(match: re.Match[str]) -> str:
    day = int(match.group(1))
    if not 1 <= day <= 31:
        return match.group(0)
    word, _ = word_before(match.string, match.start())
    if word == "на" or APPOSITION.search(match.string[: match.start()]):
        case = "n"
    elif word in DATIVE:
        case = "d"
    else:
        case = "g"
    return f"{ordinal(day, case, 'n')} {match.group(2)}"


def year(match: re.Match[str]) -> str:
    noun = match.group(2)
    if noun == "года":
        case = "g"
    elif noun == "году":
        case = "d" if case_before(match.string, match.start()) == "d" else "p"
    elif noun in ("годом", "годах"):
        case = "p"
    else:
        case = "n"
    return f"{ordinal(value(match.group(1)), case, 'm')} {noun}"


def clock(match: re.Match[str]) -> str:
    hours = cardinal(int(match.group(1)), case_before(match.string, match.start()))
    minutes = match.group(2)
    if minutes == "00":
        return f"{hours} ноль-ноль"
    if minutes.startswith("0"):
        return f"{hours} ноль {cardinal(int(minutes))}"
    return f"{hours} {cardinal(int(minutes))}"


def decimal_words(whole: int, fraction: str, case: str) -> str | None:
    if fraction == "5":
        return f"{cardinal(whole, case)} с половиной"
    if len(fraction) > len(FRACTIONS):
        return None
    part = int(fraction)
    whole_word = "целая" if is_one(whole) else "целых"
    fraction_word = FRACTIONS[len(fraction) - 1][0 if is_one(part) else 1]
    return (
        f"{cardinal(whole, case, 'f')} {whole_word} "
        f"{cardinal(part, case, 'f')} {fraction_word}"
    )


def decimal(match: re.Match[str]) -> str:
    case = case_before(match.string, match.start())
    words = decimal_words(value(match.group(1)), match.group(2), case)
    return match.group(0) if words is None else words


def decimal_amount(
    match: re.Match[str], symbol: str, digits: str, fraction: str, scale: str | None
) -> str:
    whole = value(digits)
    words = decimal_words(whole, fraction, case_before(match.string, match.start()))
    if words is None:
        return match.group(0)
    if scale is None:
        return f"{words} {decimal_count(UNITS[symbol]['n'], whole, fraction)}"
    if scale in SCALES:
        scale = decimal_count(SCALES[scale], whole, fraction)
    return f"{words} {scale} {UNITS[symbol]['g'][1]}"


def decimal_suffix_unit(match: re.Match[str]) -> str:
    return decimal_amount(match, match.group(3), match.group(1), match.group(2), None)


def decimal_prefix_unit(match: re.Match[str]) -> str:
    scale = match.group(4) or match.group(5)
    return decimal_amount(match, match.group(1), match.group(2), match.group(3), scale)


def amount(first: str | None, last: str, symbol: str, case: str) -> str:
    gender = UNIT_GENDER[symbol]
    n = value(last)
    words = cardinal(n, case, gender)
    if first is not None:
        words = f"{cardinal(value(first), case, gender)} — {words}"
    return f"{words} {unit_form(symbol, n, case)}"


def suffix_unit(match: re.Match[str]) -> str:
    case = case_before(match.string, match.start())
    return amount(match.group(1), match.group(2), match.group(3), case)


def prefix_unit(match: re.Match[str]) -> str:
    case = case_before(match.string, match.start())
    first, last = match.group(2), match.group(3)
    if last is None:
        return amount(None, first, match.group(1), case)
    return amount(first, last, match.group(1), case)


def number_range(match: re.Match[str]) -> str:
    case = case_before(match.string, match.start())
    first = cardinal(value(match.group(1)), case)
    return f"{first} — {cardinal(value(match.group(2)), case)}"


def number(match: re.Match[str]) -> str:
    return cardinal(value(match.group(0)), case_before(match.string, match.start()))


def symbols(text: str) -> str:
    text = MINUS_START.sub("минус ", text)
    text = MINUS.sub(" минус ", text)
    text = PLUS.sub(" плюс ", text)
    text = TIMES.sub(" умножить на ", text)
    text = EQUALS.sub(" равно ", text)
    return APPROX.sub("примерно ", text)


def normalize_numbers(text: str) -> str:
    text = PHONE.sub(phone, text)
    text = symbols(text)
    text = DATE.sub(date, text)
    text = YEAR.sub(year, text)
    text = TIME.sub(clock, text)
    text = DECIMAL_SUFFIX_UNIT.sub(decimal_suffix_unit, text)
    text = DECIMAL_PREFIX_UNIT.sub(decimal_prefix_unit, text)
    text = DECIMAL.sub(decimal, text)
    text = SUFFIX_UNIT.sub(suffix_unit, text)
    text = PREFIX_UNIT.sub(prefix_unit, text)
    text = RANGE.sub(number_range, text)
    return NUMBER.sub(number, text)
