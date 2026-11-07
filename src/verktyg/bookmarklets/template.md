---
# Copy this file to NN-<slug>.md in this directory. AUTHORING.md has the rules.
# Tab text and drag-button label, in every portal language. Plain text.
tab.sv: Kort fliknamn
tab.en: Short tab name
label.sv: Vad knappen gör
label.en: What the button does
---

<!-- One description per language: what it does, where it runs, how to stop or undo it. Markdown. -->

# sv

Beskriv bokmärket på svenska.

# en

Describe the bookmarklet in English.

<!-- Exactly one fenced block. Keep the locale line and the sv/en message table; see AUTHORING.md. -->

# Code

```js
javascript:(() => {
  const locale = 'sv';
  const text = {
    sv: { done: 'Klart.' },
    en: { done: 'Done.' },
  }[locale];
  console.info(text.done);
})();
```
