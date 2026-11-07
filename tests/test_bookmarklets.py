import logging
from html.parser import HTMLParser
from urllib.parse import unquote

import pytest

from verktyg import create_app
from verktyg.bookmarklets import load_directory, parse_file
from verktyg.bookmarklets.__main__ import main

VALID = """---
# A comment.
tab.sv: Flik
tab.en: Tab
label.sv: Etikett: med kolon
label.en: Label
---

<!-- Guidance for authors. -->

# sv

Gör *något*. <!-- Not shown. -->

```js
not the code
```

# en

Does `something`.

# Code

```js
javascript:(() => {
  const locale = 'sv';
  // <!-- stays in the code -->
  console.log(`${locale}`);
})();
```
"""


def write(directory, name, text=VALID):
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def validate(capsys, *arguments):
    status = main([str(argument) for argument in arguments])
    return status, capsys.readouterr()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls, self.tabs = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href", "").startswith("javascript:"):
            self.urls.append(attrs["href"])
        if attrs.get("role") == "tab":
            self.tabs.append(attrs["id"])


def page(directory, locale="sv"):
    app = create_app(
        {
            "TESTING": True,
            "DEBUG": True,
            "REQUIRE_AUTH": False,
            "BOOKMARKLETS_DIRECTORY": directory,
        }
    )
    text = app.test_client().get(f"/bookmarklets?lang={locale}").text
    links = Links()
    links.feed(text)
    return text, links


def test_the_real_directory_template_and_example_are_valid(capsys):
    status, output = validate(capsys)
    assert status == 0, output.out
    assert output.out.endswith(", 0 problems\n")


def test_a_valid_file_is_parsed(tmp_path):
    bookmarklet, problems = parse_file(write(tmp_path, "10-demo.md"))
    assert problems == []
    assert bookmarklet.slug == "demo"
    assert bookmarklet.tabs == {"sv": "Flik", "en": "Tab"}
    assert bookmarklet.labels["sv"] == "Etikett: med kolon"
    assert "<em>något</em>" in bookmarklet.descriptions["sv"]
    assert "<code>something</code>" in bookmarklet.descriptions["en"]
    # The fence in the description is part of it, not the code.
    assert "not the code" in bookmarklet.descriptions["sv"]
    assert "<!--" not in bookmarklet.descriptions["sv"]
    assert "// <!-- stays in the code -->" in bookmarklet.source
    assert bookmarklet.source.startswith("javascript:(() => {")
    assert unquote(bookmarklet.install_url("en")).count("const locale = 'en';") == 1


@pytest.mark.parametrize(
    "change, reason",
    [
        (lambda t: t.replace("---\n# A", "# A", 1), "must start with a '---'"),
        (lambda t: t.replace("label.en: Label\n---", "label.en: Label"), "no closing"),
        (lambda t: t.replace("tab.en: Tab", "tab.en Tab"), "expected 'key: value'"),
        (lambda t: t.replace("tab.en: Tab", "tab.en: Tab\ncolour: red"), "unknown"),
        (lambda t: t.replace("tab.en: Tab", "tab.en: Tab\ntab.en: Tab"), "repeated"),
        (lambda t: t.replace("tab.en: Tab", "tab.en:"), "has no value"),
        (lambda t: t.replace("tab.en: Tab\n", ""), "missing front matter key"),
        (lambda t: t.replace("# en\n", "## en\n"), "missing description section"),
        (lambda t: t + "\n# sv\n\nIgen.\n", "repeated section '# sv'"),
        (lambda t: t.replace("Does `something`.", "<!-- x -->"), "is empty"),
        (lambda t: t + "\n# Notes\n\nx\n", "unexpected heading '# Notes'"),
        (lambda t: t.replace("<!-- Guidance", "Stray <!--"), "before the first"),
        (lambda t: t[: t.index("# Code")], "missing code section"),
        (lambda t: t + "\n```js\nmore\n```\n", "exactly one fenced code block"),
        (lambda t: t + "\nAfter.\n", "text outside its code block"),
        (lambda t: t.replace("```js\njavascript", "```py\njavascript"), "language"),
        (lambda t: t.replace("`${locale}`", "```x```"), "longest backtick run"),
        (lambda t: t.replace("javascript:(()", "(()"), "start with 'javascript:'"),
        (lambda t: t.replace("const locale = 'sv';", ""), "exactly once"),
        (
            lambda t: (
                t[: t.index("javascript:")] + "javascript:(()%3D%3E%7B%7D)()\n```\n"
            ),
            "percent-encoded",
        ),
    ],
)
def test_each_rule_is_reported(tmp_path, capsys, change, reason):
    path = write(tmp_path, "10-demo.md", change(VALID))
    status, output = validate(capsys, path)
    assert status == 1
    problems = output.out.splitlines()[:-1]
    assert any(line.startswith(f"{path}: ") and reason in line for line in problems), (
        output.out
    )


def test_bad_slug_and_bad_encoding_are_reported(tmp_path, capsys):
    bad_slug = write(tmp_path, "10-Demo.md")
    bad_bytes = tmp_path / "20-latin.md"
    bad_bytes.write_bytes(VALID.replace("Gör", "G\xf6r").encode("latin-1"))
    status, output = validate(capsys, bad_slug, bad_bytes)
    assert status == 1
    assert f"{bad_slug}: slug 'Demo' must be lowercase" in output.out
    assert f"{bad_bytes}: the file is not valid UTF-8" in output.out
    assert output.out.endswith("2 files, 2 problems\n")


def test_every_problem_in_a_file_is_reported(tmp_path, capsys):
    text = VALID.replace("tab.en: Tab\n", "").replace("# en\n", "# fr\n")
    path = write(tmp_path, "10-demo.md", text.replace("javascript:(()", "(()"))
    status, output = validate(capsys, path)
    assert status == 1
    assert output.out.endswith("1 file, 4 problems\n")


def test_drafts_outside_the_directory_are_validated(tmp_path, capsys):
    assert validate(capsys, write(tmp_path, "draft.md"))[0] == 0
    assert validate(capsys, write(tmp_path, "90-draft.md"))[0] == 0
    status, output = validate(capsys, write(tmp_path, "90-avprickning.md"))
    assert status == 1
    assert "slug 'avprickning' is also used by" in output.out


@pytest.mark.parametrize("argument", ["missing.md", "."])
def test_a_missing_file_or_a_directory_is_a_usage_error(tmp_path, capsys, argument):
    status, output = validate(capsys, tmp_path / argument)
    assert status == 2
    assert "usage:" in output.err


def test_files_are_ordered_by_prefix_then_slug_and_others_ignored(tmp_path):
    for name in ("20-b.md", "10-z.md", "20-a.md"):
        write(tmp_path, name)
    for name in ("template.md", "1-x.md", "100-x.md", "10_x.md", "10-x.txt", "x.md"):
        write(tmp_path, name, "not a bookmarklet")
    (tmp_path / "30-dir.md").mkdir()
    bookmarklets, problems = load_directory(tmp_path)
    assert problems == []
    assert [b.slug for b in bookmarklets] == ["z", "a", "b"]


def test_an_empty_directory_renders_no_tabs(tmp_path):
    text, links = page(tmp_path)
    assert links.tabs == [] and 'role="tablist"' not in text
    assert "Installera" in text


def test_invalid_files_are_logged_and_skipped(tmp_path, caplog):
    write(tmp_path, "10-good.md")
    write(tmp_path, "20-bad.md", VALID.replace("# Code", "# Kod"))
    with caplog.at_level(logging.ERROR):
        text, links = page(tmp_path, "en")
    assert links.tabs == ["good-tab"]
    assert "Tab" in text and "Does <code>something</code>" in text
    assert "20-bad.md: line" in caplog.text and "missing code section" in caplog.text


def test_files_sharing_a_slug_are_both_skipped(tmp_path, caplog):
    write(tmp_path, "10-same.md")
    write(tmp_path, "20-same.md")
    write(tmp_path, "30-other.md")
    with caplog.at_level(logging.ERROR):
        _, links = page(tmp_path)
    assert links.tabs == ["other-tab"]
    assert "slug 'same' is also used by 20-same.md" in caplog.text


def test_an_unreadable_directory_does_not_stop_the_portal(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        _, links = page(tmp_path / "missing")
    assert links.tabs == []
    assert "cannot read the directory" in caplog.text
