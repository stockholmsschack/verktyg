const text = JSON.parse(document.getElementById("bookmarklet-text").textContent);

for (const card of document.querySelectorAll("[data-bookmarklet]")) {
  const installStatus = card.querySelector("[data-status]");
  const copyButton = card.querySelector("[data-copy]");
  const copyLabel = copyButton.querySelector("span");
  const bookmarkletCode = card.querySelector("[data-code]");

  // Running the bookmarklet here would fail; clicking explains how to install it.
  card.querySelector("[data-drag]").addEventListener("click", function (event) {
    event.preventDefault();
    installStatus.textContent = text.clicked;
  });

  copyButton.addEventListener("click", async function () {
    if (copyButton.disabled) return;

    copyButton.disabled = true;
    copyLabel.textContent = text.copying;
    try {
      await navigator.clipboard.writeText(bookmarkletCode.value);
      copyLabel.textContent = text.copied;
      installStatus.textContent = text.copy_done;
    } catch {
      card.querySelector("[data-details]").open = true;
      bookmarkletCode.focus();
      bookmarkletCode.select();
      copyLabel.textContent = text.copy;
      installStatus.textContent = text.copy_failed;
    } finally {
      copyButton.disabled = false;
      setTimeout(function () {
        copyLabel.textContent = text.copy;
      }, 2000);
    }
  });
}
