"""Validate bookmarklet files: `python -m verktyg.bookmarklets [FILE ...]`.

With no files, check every bookmarklet file in the package directory, plus
`template.md` and `example.md`. Prints one `<path>: <reason>` line per
problem and a summary; exits 0 when clean, 1 on problems, 2 on usage errors.
"""

import sys
from pathlib import Path

from . import (
    BOOKMARKLET_NAME,
    DIRECTORY,
    Problem,
    is_bookmarklet_file,
    load_directory,
    parse_file,
)

USAGE = "usage: python -m verktyg.bookmarklets [FILE ...]"
# Not bookmarklets, but kept parseable so they never drift from the rules.
REFERENCE_FILES = ("template.md", "example.md")


def validate_directory() -> tuple[int, list[Problem]]:
    _, problems = load_directory(DIRECTORY)
    count = sum(1 for path in DIRECTORY.iterdir() if is_bookmarklet_file(path))
    for name in REFERENCE_FILES:
        problems += parse_file(DIRECTORY / name)[1]
    return count + len(REFERENCE_FILES), problems


def validate_files(paths: list[Path]) -> list[Problem]:
    problems = []
    existing = {
        BOOKMARKLET_NAME.fullmatch(path.name)[2]: path
        for path in DIRECTORY.iterdir()
        if is_bookmarklet_file(path)
    }
    for path in paths:
        problems += parse_file(path)[1]
        name = BOOKMARKLET_NAME.fullmatch(path.name)
        other = existing.get(name[2]) if name else None
        if other and other.resolve() != path.resolve():
            problems.append(Problem(path, f"slug {name[2]!r} is also used by {other}"))
    return problems


def relative(problem: Problem) -> Problem:
    """Show paths below the working directory as relative paths."""
    try:
        path = problem.path.resolve().relative_to(Path.cwd())
    except ValueError:
        return problem
    return Problem(path, problem.reason, problem.line)


def main(arguments: list[str]) -> int:
    if any(argument.startswith("-") for argument in arguments):
        print(USAGE, file=sys.stderr)
        return 2
    paths = [Path(argument) for argument in arguments]
    for path in paths:
        if not path.is_file():
            print(f"{path}: not a file\n{USAGE}", file=sys.stderr)
            return 2
    if paths:
        count, problems = len(paths), validate_files(paths)
    else:
        count, problems = validate_directory()
    problems.sort(key=lambda problem: (str(problem.path), problem.line or 0))
    for problem in problems:
        print(relative(problem))
    files = "file" if count == 1 else "files"
    found = "problem" if len(problems) == 1 else "problems"
    print(f"{count} {files}, {len(problems)} {found}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
