"""Readable MAX text; persisted business text remains unchanged."""

import html
import re

COMMAND = re.compile(
    r"(?<![\w:/])/(?:menu|ask|hr|resume|cases|food|places|materials|plan|events|cancel|help|start|admin|dashboard|reply|close)\b"
)
LABEL = re.compile(r"^(Ваш вопрос|Ответ HR|Решение HR|Сотрудник|Площадка|Срок|Результат|Источники|Контакты):")


def normalize(text):
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    # Split existing command enumerations, not URLs or ordinary punctuation.
    text = re.sub(
        r"[,;]\s+(?=/(?:menu|ask|hr|resume|cases|food|places|materials|plan|events|cancel|help|admin|dashboard)\b)",
        "\n",
        text,
    )
    text = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "• ", text)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()


def as_html(text):
    lines = normalize(text).split("\n")
    output = []
    for index, line in enumerate(lines):
        escaped = html.escape(line, quote=False)
        escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", escaped)
        if LABEL.match(line):
            escaped = LABEL.sub(lambda m: "<b>" + m[0] + "</b>", escaped, count=1)
        elif (
            line
            and len(line) <= 90
            and not line.startswith(("/", "•", "-", "<"))
            and not re.match(r"^\d+[.)]", line)
            and (line.endswith(":") or index == 0 and len(lines) > 1 and "<b>" not in escaped)
        ):
            escaped = "<b>" + escaped + "</b>"
        # Do not nest code tags inside a code span originating from Markdown.
        segments = re.split(r"(<code>.*?</code>)", escaped)
        escaped = "".join(
            s if s.startswith("<code>") else COMMAND.sub(lambda m: "<code>" + m[0] + "</code>", s)
            for s in segments
        )
        output.append(escaped)
    return "\n".join(output)


def units(text):
    return len(text.encode("utf-16-le")) // 2


def split_message(text, limit=3500):
    """Split at paragraphs/lines/words, accounting for escaped HTML and emoji size."""
    remaining = normalize(text)
    result = []
    while remaining:
        if units(as_html(remaining)) <= limit:
            result.append(remaining)
            break
        low, high = 1, min(len(remaining), limit)
        while low < high:
            middle = (low + high + 1) // 2
            if units(as_html(remaining[:middle])) <= limit:
                low = middle
            else:
                high = middle - 1
        cut = low
        for separator in ("\n\n", "\n", " "):
            boundary = remaining.rfind(separator, 0, cut)
            if boundary >= cut // 2:
                cut = boundary
                break
        while cut > 0 and units(as_html(remaining[:cut].strip())) > limit:
            cut -= 1
        part = remaining[:cut].strip()
        if not part:
            raise ValueError("Message could not fit the presentation limit")
        result.append(part)
        remaining = remaining[cut:].lstrip()
    return result or [""]
