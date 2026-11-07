# Authoring bookmarklets

This guide is the single description of the bookmarklet file format. It is
written for maintainers and for agents. Follow it exactly.

Every bookmarklet on `/bookmarklets` is one Markdown file in this directory,
`src/verktyg/bookmarklets/`. The portal reads the files when it starts, so
restart it (or redeploy) after adding or changing one.

## Inputs

To write a bookmarklet file you need:

- the JavaScript source of the bookmarklet;
- a description of what it does, in any language;
- optionally, a position among the existing tabs.

## File names

- Name the file `NN-<slug>.md`.
- `NN` is two digits, `00` to `99`. Tabs are sorted by it, then by slug.
  Existing files leave gaps (`10`, `20`, …). Unless asked for a position, use
  the next multiple of ten after the highest existing prefix.
- `<slug>` names the bookmarklet in the page's HTML ids. Choose it from the
  bookmarklet's purpose, in lowercase `a-z` and `0-9` words joined by single
  hyphens, for example `klubb-betalning`. Never rename an existing slug.
- Files without an `NN-` prefix are ignored, including this guide,
  `template.md` and `example.md`. A file with an `NN-` prefix and a bad slug,
  such as `30-Club.md`, is reported as invalid, not ignored.

## File format

A bookmarklet file has three parts.

1. **Front matter**, between two `---` lines at the very top. Each line is
   `key: value`. Blank lines and lines starting with `#` are comments. Values
   are plain text, not Markdown, and may contain `:`. The required keys, one
   per portal language (`sv`, `en`), are:
   - `tab.sv`, `tab.en`: the short tab name;
   - `label.sv`, `label.en`: the text on the drag button.
2. **Descriptions**: a level-1 heading per language, exactly `# sv` and
   `# en`. The Markdown below it, up to the next level-1 heading, is that
   language's description. Raw HTML is allowed.
3. **Code**: a level-1 heading `# Code` followed by exactly one fenced code
   block with the language `js` or `javascript`. Its content is the
   bookmarklet source.

The sections may come in any order. HTML comments (`<!-- … -->`) may appear
anywhere outside the code block and are removed before the page shows the
descriptions.

## Validation rules

The validator and the portal report a file as invalid when:

- its slug is not lowercase `a-z`/`0-9` words joined by single hyphens;
- another file uses the same slug (both are then skipped by the portal);
- the file is not valid UTF-8;
- the front matter is missing, not closed, has a line that is not
  `key: value`, has an unknown or repeated key, has a key with no value, or
  lacks a required key;
- a description section is missing, repeated or empty, or there is a level-1
  heading other than `# sv`, `# en` and `# Code`;
- there is text other than comments before the first section;
- the code section is missing, does not contain exactly one fenced code
  block, or has text outside it;
- the code block's language is not `js` or `javascript`;
- the code fence is not longer than the longest backtick run in the source;
- the source does not start with `javascript:`;
- the source does not contain `const locale = 'sv';` exactly once;
- the source looks percent-encoded (it contains `%XX` escapes and no
  whitespace).

## The source

- The source starts with `javascript:` and is the readable code, not an
  install URL. The portal percent-encodes it.
- It contains exactly one line `const locale = 'sv';`. The portal replaces
  `'sv'` with the reader's language, so the installed bookmark speaks that
  language.
- All user-facing text lives in a message table with `sv` and `en` entries
  selected by `locale`:

  ```js
  const text = {
    sv: { done: 'Klart.' },
    en: { done: 'Done.' },
  }[locale];
  ```

## Procedure

1. If the source is a percent-encoded install URL (it contains `%20`, `%7B`
   and so on, and no spaces), decode it first.
2. Make sure the source starts with `javascript:`.
3. Make sure it has exactly one `const locale = 'sv';` line and a message
   table with `sv` and `en` entries keyed by `locale`. If it has no such
   table, move its user-facing strings into one, changing nothing else, and
   say so in the hand-off.
4. Write the description in Swedish and English, translating where needed.
   Keep it to what the bookmarklet does, the page it runs on, and how to stop
   or undo it. Use `*emphasis*` for button names and backticks for code.
5. Write a short tab text and a drag-button label in both languages.
6. Choose a code fence of backticks longer than any backtick run in the
   source. Three backticks are enough when the source only has single
   backticks (template literals).
7. Copy `template.md` to `NN-<slug>.md` and fill it in. Keep or remove the
   guidance comments; they never reach the page. `example.md` shows a
   complete file.
8. Run the validator on the new file and fix every problem it reports. Repeat
   until it exits with status `0`:

   ```sh
   uv run python -m verktyg.bookmarklets src/verktyg/bookmarklets/NN-<slug>.md
   ```

9. Run the whole check and the test suite:

   ```sh
   uv run python -m verktyg.bookmarklets
   uv run pytest
   ```

## Do not

- change what the source does, beyond moving strings into the message table;
- add network calls to the source;
- edit other bookmarklet files, the page template or the locale catalogues.

## The validator

```sh
uv run python -m verktyg.bookmarklets            # this directory, plus template and example
uv run python -m verktyg.bookmarklets FILE ...   # only these files, anywhere
```

It prints one `<path>: <reason>` line per problem, with `line N:` where the
line is known, then a summary such as `4 files, 0 problems`. It exits with
`0` when there are no problems, `1` when there are, and `2` on a usage error
such as a missing file. A file given by path that is named `NN-<slug>.md` is
also checked for a slug used by another file in this directory.

The portal applies the same rules. It skips an invalid file, logs each problem
at error level, and serves the other bookmarklets.
