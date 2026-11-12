import re
from collections.abc import AsyncIterable, AsyncIterator

TOOL_CALL_START = "<|tool_call_start|>"
TOOL_CALL_END = "<|tool_call_end|>"
TABLE_SEPARATOR = re.compile(r"[|\-:\s]+")
LATEX = (
    re.compile(r"\\\((.+?)\\\)"),
    re.compile(r"\\\[(.+?)\\\]"),
    re.compile(r"\$\$(.+?)\$\$"),
    re.compile(r"\$(?!\d)(?=[^$]*[\\^_{])([^$]+)\$"),
)
FRAC = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
TIMES = re.compile(r"\\(?:times|cdot)(?![a-zA-Z])")
POWER = re.compile(r"\^\{([^{}]*)\}|\^(\d+|\w)")
COMMAND = re.compile(r"\\[a-zA-Z]+")
BRACES = re.compile(r"[{}_]")


def held(buffer: str, marker: str) -> int:
    for size in range(min(len(buffer), len(marker) - 1), 0, -1):
        if buffer.endswith(marker[:size]):
            return size
    return 0


def scan(buffer: str, inside: bool) -> tuple[str, str, bool]:
    out: list[str] = []
    while True:
        marker = TOOL_CALL_END if inside else TOOL_CALL_START
        index = buffer.find(marker)
        if index >= 0:
            if not inside:
                out.append(buffer[:index])
            buffer = buffer[index + len(marker) :]
            inside = not inside
            continue
        keep = len(buffer) - held(buffer, marker)
        if not inside:
            out.append(buffer[:keep])
        return "".join(out), buffer[keep:], inside


async def strip_tool_calls(chunks: AsyncIterable[str]) -> AsyncIterator[str]:
    buffer = ""
    inside = False
    async for chunk in chunks:
        text, buffer, inside = scan(buffer + chunk, inside)
        if text:
            yield text
    if buffer and not inside:
        yield buffer


def latex_body(body: str) -> str:
    body = FRAC.sub(r"\1 делить на \2", body)
    body = TIMES.sub(" умножить на ", body)
    body = POWER.sub(lambda m: f" в степени {m.group(1) or m.group(2)}", body)
    body = COMMAND.sub("", body)
    body = BRACES.sub("", body)
    return " ".join(body.split())


def clean_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("|"):
        if TABLE_SEPARATOR.fullmatch(stripped):
            return ""
        cells = [cell.strip() for cell in stripped.split("|")]
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]
        line = ", ".join(cells) + "."
    for pattern in LATEX:
        line = pattern.sub(lambda m: latex_body(m.group(1)), line)
    return line


async def strip_latex_and_tables(
    chunks: AsyncIterable[str],
) -> AsyncIterator[str]:
    buffer = ""
    async for chunk in chunks:
        buffer += chunk
        *lines, buffer = buffer.split("\n")
        for line in lines:
            cleaned = clean_line(line)
            if cleaned or not line.strip():
                yield cleaned + "\n"
    if buffer:
        yield clean_line(buffer)
