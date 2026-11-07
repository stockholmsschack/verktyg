"""Bookmarklets defined by `NN-<slug>.md` files in this directory.

`AUTHORING.md` describes the format. The portal and the validator
(`python -m verktyg.bookmarklets`) both parse files here, so they agree.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from markdown_it import MarkdownIt

from ..i18n import LOCALES

DIRECTORY = Path(__file__).parent
# Each bookmarklet source starts in Swedish; the portal sets the reader's locale.
SOURCE_LOCALE = "const locale = 'sv';"
BOOKMARKLET_NAME = re.compile(r"(\d{2})-(.*)\.md")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KEYS = {f"{field}.{locale}" for field in ("tab", "label") for locale in LOCALES}
CODE_HEADING = "Code"
CODE_LANGUAGES = {"js", "javascript"}
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
MARKDOWN = MarkdownIt("commonmark")


@dataclass(frozen=True)
class Problem:
    path: Path
    reason: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{self.path}: {where}{self.reason}"


@dataclass(frozen=True)
class Bookmarklet:
    prefix: str
    slug: str
    tabs: dict[str, str]
    labels: dict[str, str]
    # Rendered HTML, by locale.
    descriptions: dict[str, str]
    source: str

    def install_url(self, locale: str) -> str:
        code = self.source.removeprefix("javascript:")
        # A saved bookmarklet keeps the language chosen when it was installed.
        code = code.replace(SOURCE_LOCALE, SOURCE_LOCALE.replace("sv", locale), 1)
        return "javascript:" + quote(code, safe="")


def is_bookmarklet_file(path: Path) -> bool:
    return path.is_file() and BOOKMARKLET_NAME.fullmatch(path.name) is not None


def parse_file(path: Path) -> tuple[Bookmarklet | None, list[Problem]]:
    """Parse one file, whatever its name, and report every problem in it."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None, [Problem(path, "the file is not valid UTF-8")]
    except OSError as error:
        return None, [Problem(path, f"cannot read the file: {error.strerror}")]
    problems = []

    def problem(reason: str, line: int | None = None) -> None:
        problems.append(Problem(path, reason, line))

    name = BOOKMARKLET_NAME.fullmatch(path.name)
    prefix, slug = name.groups() if name else ("", path.stem)
    if name and not SLUG.fullmatch(slug):
        problem(
            f"slug {slug!r} must be lowercase a-z and 0-9 words joined by single hyphens"
        )
    lines = text.splitlines()
    values, body_start = parse_front_matter(lines, problem)
    sections = parse_body(lines[body_start:], body_start, problem)

    descriptions = {}
    for locale in LOCALES:
        if locale not in sections:
            problem(f"missing description section '# {locale}'")
            continue
        start, end, _ = sections[locale]
        markdown = HTML_COMMENT.sub("", "\n".join(lines[start:end]))
        if not markdown.strip():
            problem(f"description section '# {locale}' is empty", start)
        descriptions[locale] = MARKDOWN.render(markdown)
    source = parse_code(sections, lines, body_start, problem)

    if problems:
        return None, problems
    return (
        Bookmarklet(
            prefix=prefix,
            slug=slug,
            tabs={locale: values[f"tab.{locale}"] for locale in LOCALES},
            labels={locale: values[f"label.{locale}"] for locale in LOCALES},
            descriptions=descriptions,
            source=source,
        ),
        [],
    )


def parse_front_matter(lines: list[str], problem) -> tuple[dict[str, str], int]:
    """Return the front matter's values and the index of the body's first line."""
    if not lines or lines[0].strip() != "---":
        problem("the file must start with a '---' front matter line", 1)
        return {}, 0
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        problem("the front matter has no closing '---' line", 1)
        return {}, len(lines)
    values, seen = {}, set()
    for index in range(1, end):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            continue
        key, colon, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not colon:
            problem(f"expected 'key: value', found {line!r}", index + 1)
        elif key not in KEYS:
            problem(f"unknown front matter key {key!r}", index + 1)
        elif key in seen:
            problem(f"repeated front matter key {key!r}", index + 1)
        else:
            seen.add(key)
            if value:
                values[key] = value
            else:
                problem(f"front matter key {key!r} has no value", index + 1)
    for key in sorted(KEYS - seen):
        problem(f"missing front matter key {key!r}", 1)
    return values, end + 1


def parse_body(lines: list[str], offset: int, problem) -> dict:
    """Map each level-1 heading to the section after it: (start, end, tokens).

    Start and end index the whole file's lines. The section's top-level
    Markdown tokens have maps relative to `offset`. Text before the first
    heading may only be comments.
    """
    tokens = MARKDOWN.parse("\n".join(lines))
    headings = [
        (index, token.map[0], tokens[index + 1].content.strip())
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h1" and token.level == 0
    ]
    first_heading = headings[0][1] if headings else len(lines)
    if HTML_COMMENT.sub("", "\n".join(lines[:first_heading])).strip():
        problem("text before the first '#' section", offset + 1)
    allowed = {*LOCALES, CODE_HEADING}
    sections = {}
    for position, (index, line, title) in enumerate(headings):
        if position + 1 < len(headings):
            next_index, end, _ = headings[position + 1]
        else:
            next_index, end = len(tokens), len(lines)
        if title not in allowed:
            problem(
                f"unexpected heading '# {title}'; expected one of "
                + ", ".join(f"'# {name}'" for name in (*LOCALES, CODE_HEADING)),
                offset + line + 1,
            )
        elif title in sections:
            problem(f"repeated section '# {title}'", offset + line + 1)
        else:
            # The heading's own tokens are open, inline and close.
            start = tokens[index].map[1]
            section_tokens = [t for t in tokens[index + 3 : next_index] if t.level == 0]
            sections[title] = (offset + start, offset + end, section_tokens)
    return sections


def parse_code(sections: dict, lines: list[str], offset: int, problem) -> str:
    if CODE_HEADING not in sections:
        problem(f"missing code section '# {CODE_HEADING}'")
        return ""
    start, end, tokens = sections[CODE_HEADING]
    fences = [token for token in tokens if token.type == "fence"]
    if len(fences) != 1:
        problem(
            f"section '# {CODE_HEADING}' must contain exactly one fenced code block,"
            f" found {len(fences)}",
            start,
        )
        return ""
    fence = fences[0]
    fence_start, fence_end = (offset + line for line in fence.map)
    outside = lines[start:fence_start] + lines[fence_end:end]
    if HTML_COMMENT.sub("", "\n".join(outside)).strip():
        problem(
            f"section '# {CODE_HEADING}' has text outside its code block; is the"
            " fence shorter than a backtick run in the source?",
            start,
        )
    language = fence.info.strip().split(" ")[0]
    if language not in CODE_LANGUAGES:
        problem(
            f"the code block's language must be 'js' or 'javascript', not {language!r}",
            fence_start + 1,
        )
    source = fence.content.strip()
    longest_run = max((len(run) for run in re.findall(r"`+", source)), default=0)
    if fence.markup.startswith("`") and longest_run >= len(fence.markup):
        problem(
            f"the code fence ({len(fence.markup)} backticks) must be longer than the"
            f" source's longest backtick run ({longest_run})",
            fence_start + 1,
        )
    if not source.startswith("javascript:"):
        problem("the source must start with 'javascript:'", fence_start + 2)
    locale_lines = source.count(SOURCE_LOCALE)
    if locale_lines != 1:
        problem(
            f'the source must contain "{SOURCE_LOCALE}" exactly once,'
            f" found {locale_lines}",
            fence_start + 2,
        )
    if PERCENT_ESCAPE.search(source) and not any(c.isspace() for c in source):
        problem(
            "the source looks percent-encoded; decode the install URL and paste"
            " the source",
            fence_start + 2,
        )
    return source


def load_directory(
    directory: Path = DIRECTORY,
) -> tuple[list[Bookmarklet], list[Problem]]:
    """Parse every bookmarklet file, sorted by prefix then slug.

    Invalid files, and every file sharing a slug with another, are left out
    and their problems returned.
    """
    try:
        paths = sorted(
            path for path in directory.iterdir() if is_bookmarklet_file(path)
        )
    except OSError as error:
        return [], [Problem(directory, f"cannot read the directory: {error.strerror}")]
    bookmarklets, problems = [], []
    for path in paths:
        bookmarklet, file_problems = parse_file(path)
        problems += file_problems
        if bookmarklet:
            bookmarklets.append((path, bookmarklet))
    by_slug = {}
    for path in paths:
        by_slug.setdefault(BOOKMARKLET_NAME.fullmatch(path.name)[2], []).append(path)
    shared = set()
    for slug, same in by_slug.items():
        if len(same) > 1:
            shared.update(same)
            for path in same:
                others = ", ".join(other.name for other in same if other != path)
                problems.append(
                    Problem(path, f"slug {slug!r} is also used by {others}")
                )
    valid = [bookmarklet for path, bookmarklet in bookmarklets if path not in shared]
    valid.sort(key=lambda bookmarklet: (bookmarklet.prefix, bookmarklet.slug))
    return valid, problems
