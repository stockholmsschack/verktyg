---
tab.sv: Avprickning
tab.en: Check-in
label.sv: Pricka av betalda spelare
label.en: Check in paid players
---

# sv

Markerar betalda spelare som närvarande på avprickningssidan. Öppna tävlingens avprickningssida och klicka på bokmärket. Spelare som redan är närvarande eller frånvarande hoppas över. Närvarande-rutorna blir gröna när de sparats. Avbryt genom att köra `window.__autoRegisterPresence.cancel()` i webbläsarens konsol.

# en

Marks paid players as present on the check-in page. Open the tournament's check-in page and click the bookmark. Players already present or absent are skipped. The Present boxes turn green when saved. Cancel by running `window.__autoRegisterPresence.cancel()` in the browser console.

# Code

```js
javascript:(() => {
  const locale = 'sv';
  const text = {
    sv: {
      running: 'Närvaroregistreringen körs redan.',
      wrongPage: 'sendOnSite() finns inte. Kör skriptet på avprickningssidan.',
      unknown: 'Okänd spelare',
      none: 'Inga betalda spelare behöver registreras som närvarande.',
      cancelled: (run) => `Närvaroregistreringen avbröts: ${run.registered} registrerade, ${run.skipped} överhoppade.`,
      starting: (count, ms) => `Registrerar närvaro för ${count} betalda spelare, en var ${ms}:e ms.`,
      cancel: 'Avbryt med window.__autoRegisterPresence.cancel().',
      done: (run) => `Klart: ${run.registered} registrerade, ${run.skipped} överhoppade. Kontrollera att deras Närvarande-rutor har grön bakgrund.`,
      registered: (name, run, count) => `Registrerade ${name} (${run.registered}/${count}).`,
      skipped: (name) => `Hoppade över ${name}; spelarens status har ändrats.`,
    },
    en: {
      running: 'Check-in is already running.',
      wrongPage: 'sendOnSite() does not exist. Run the script on the check-in page.',
      unknown: 'Unknown player',
      none: 'No paid players need to be checked in.',
      cancelled: (run) => `Check-in cancelled: ${run.registered} checked in, ${run.skipped} skipped.`,
      starting: (count, ms) => `Checking in ${count} paid players, one every ${ms} ms.`,
      cancel: 'Cancel with window.__autoRegisterPresence.cancel().',
      done: (run) => `Done: ${run.registered} checked in, ${run.skipped} skipped. Check that their Present boxes have a green background.`,
      registered: (name, run, count) => `Checked in ${name} (${run.registered}/${count}).`,
      skipped: (name) => `Skipped ${name}; the player's status has changed.`,
    },
  }[locale];
  const intervalMs = 3;
  const runKey = '__autoRegisterPresence';
  if (window[runKey]?.running) {
    console.warn(text.running);
    return;
  }
  if (typeof window.sendOnSite !== 'function') {
    throw new Error(text.wrongPage);
  }
  const players = Array.from(document.querySelectorAll('table.js-sort-table tr'))
    .map((row) => ({
      name: row.cells[1]?.textContent.trim() || text.unknown,
      paid: row.querySelector('input[type="checkbox"][id^="paid"]'),
      present: row.querySelector('input[type="checkbox"][id^="atvenue"]:not([id^="atvenueNOT"])'),
      willNotAttend: row.querySelector('input[type="checkbox"][id^="atvenueNOT"]'),
    }))
    .filter(({ paid, present, willNotAttend }) => paid?.checked && present && !present.checked && !willNotAttend?.checked);
  if (players.length === 0) {
    console.info(text.none);
    return;
  }
  const run = {
    running: true,
    registered: 0,
    skipped: 0,
    cancel() {
      this.running = false;
      console.warn(text.cancelled(this));
    },
  };
  window[runKey] = run;
  console.info(text.starting(players.length, intervalMs), text.cancel);
  let index = 0;
  const registerNext = () => {
    if (!run.running) return;
    if (index >= players.length) {
      run.running = false;
      console.info(text.done(run));
      return;
    }
    const player = players[index++];
    if (player.paid.checked && !player.present.checked && !player.present.disabled && !player.willNotAttend?.checked) {
      player.present.click();
      run.registered++;
      console.info(text.registered(player.name, run, players.length));
    } else {
      run.skipped++;
      console.info(text.skipped(player.name));
    }
    window.setTimeout(registerNext, intervalMs);
  };
  registerNext();
})();
```
