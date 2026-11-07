---
tab.sv: Exempel
tab.en: Example
label.sv: Säg hej
label.en: Say hello
---

# sv

Visar en hälsning med sidans titel. Klicka på bokmärket på vilken sida som
helst och stäng rutan med *OK*. Bokmärket ändrar ingenting på sidan.

# en

Shows a greeting with the page's title. Click the bookmark on any page and
close the box with *OK*. The bookmark changes nothing on the page.

# Code

```js
javascript:(() => {
  const locale = 'sv';
  const text = {
    sv: { hello: (title) => `Hej från ${title}!` },
    en: { hello: (title) => `Hello from ${title}!` },
  }[locale];
  alert(text.hello(document.title));
})();
```
