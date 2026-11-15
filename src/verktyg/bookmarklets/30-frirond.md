---
tab.sv: Frirond
tab.en: Frirond
label.sv: Frirond
label.en: Frirond
---

# sv

Registrerar vinst på w.o. på varje bord för laget som möter Frirond. Alla bord får 0–1 w.o. om Frirond står till vänster, eller 1–0 w.o. om Frirond står till höger. Öppna matchrapporten och spara laguppställningarna med *Spara spelare* innan du klickar på bokmärket.

Resultaten sparas direkt, ett bord i taget. Redan korrekta resultat lämnas kvar. Om något bord har ett annat resultat avbryts körningen utan ändringar. Kontrollera resultaten efteråt; sparade resultat måste rättas i matchrapporten.

# en

Records a walkover win on every board for the team paired against Frirond. All boards receive 0–1 w.o. when Frirond is on the left, or 1–0 w.o. when Frirond is on the right. Open the match report and save the rosters with *Spara spelare* before clicking the bookmark.

Results are saved immediately, one board at a time. Correct results are kept. If any board has a different result, the bookmarklet stops without changes. Check the results afterwards; saved results must be corrected in the match report.

# Code

```js
javascript:(async () => {
  const locale = 'sv';
  const text = {
    sv: {
      wrongPage: 'Öppna matchrapporten för en lagtävling först.',
      noBye: 'Exakt ett av lagen i matchen måste vara Frirond.',
      locked: 'Resultaten är låsta. Spara laguppställningarna med "Spara spelare" först.',
      futureRound: 'Ronddatum är i framtiden, du kommer inte att kunna spara individuella resultaten!\n\nRonddatum kan ändras under "Lottning" i gruppen, inte via "Hantera Ronder".',
      invalid: 'Resultatfälten kunde inte identifieras. Inga resultat ändrades.',
      conflict: board => `Bord ${board} har redan ett annat resultat. Inga resultat ändrades.`,
      failed: board => `Kunde inte bekräfta att bord ${board} sparades. Körningen har stoppats. Ladda om sidan och kontrollera resultaten innan du försöker igen. Tidigare bord kan ha sparats.`,
      done: count => `${count} w.o.-resultat sparades. Kontrollera resultaten i matchrapporten.`,
      unchanged: 'Alla bord har redan rätt w.o.-resultat.',
      error: 'Frirond: ',
    },
    en: {
      wrongPage: 'Open a team tournament match report first.',
      noBye: 'Exactly one team in the match must be Frirond.',
      locked: 'Results are locked. Save the rosters with "Spara spelare" first.',
      futureRound: 'The round date is in the future; you will not be able to save individual results!\n\nThe round date can be changed under "Lottning" in the group, not via "Hantera Ronder".',
      invalid: 'Could not identify the result fields. No results were changed.',
      conflict: board => `Board ${board} already has a different result. No results were changed.`,
      failed: board => `Could not confirm that board ${board} was saved. Stopped. Reload the page and check the results before trying again. Earlier boards may have been saved.`,
      done: count => `${count} walkover results saved. Check the results in the match report.`,
      unchanged: 'All boards already have the correct walkover result.',
      error: 'Frirond: ',
    },
  }[locale];
  if (window.verktygFrirondRunning) return;
  window.verktygFrirondRunning = true;
  try {
    const form = document.getElementById('updateTournamentRoundResult');
    const matchId = form?.querySelector('input[name=id]')?.value;
    if (!/^\d+$/.test(matchId || '')) throw new Error(text.wrongPage);
    if (document.body.innerText.replace(/\s+/g, ' ').includes('Ronddatum är i framtiden')) {
      throw new Error(text.futureRound);
    }

    const header = document.getElementById(`homeresult${matchId}`)?.closest('tr');
    const teams = header ? Array.from(header.querySelectorAll('td.listheader span'))
      .map(span => span.textContent.trim()).filter(name => name !== '-') : [];
    if (teams.length !== 2) throw new Error(text.invalid);
    const homeBye = teams[0].toLowerCase() === 'frirond';
    const awayBye = teams[1].toLowerCase() === 'frirond';
    if (homeBye === awayBye) throw new Error(text.noBye);

    const results = Array.from(form.querySelectorAll(`select[id^="result${matchId}"]`));
    if (!results.length || !window.jQuery) throw new Error(text.invalid);
    // Frirond results follow the left/right team order on every board.
    const value = homeBye ? '-2' : '2';
    const changes = [];
    for (const [board, select] of results.entries()) {
      if (select.id !== `result${matchId}${board}` ||
          !select.querySelector(`option[value="${value}"]`) || typeof select.onchange !== 'function') {
        throw new Error(text.invalid);
      }
      if (select.disabled) throw new Error(text.locked);
      if (select.value !== '-100' && select.value !== value) throw new Error(text.conflict(board + 1));
      if (select.value !== value) changes.push({ select, value, board });
    }

    // Let the page save each result and update its timestamps and team scores.
    // Await its response before starting another board or reporting success.
    for (const { select, value, board } of changes) {
      await new Promise((resolve, reject) => {
        const events = jQuery(document);
        const finish = error => {
          clearTimeout(timer);
          events.off('ajaxComplete.verktygFrirond', complete);
          error ? reject(error) : resolve();
        };
        const complete = (event, xhr, settings) => {
          if (!settings.url.endsWith('RegisterTournamentResultController/UpdateTeamResult')) return;
          let request;
          try { request = JSON.parse(settings.data); } catch { return; }
          if (String(request.resultid) !== matchId || Number(request.tablenr) !== board) return;
          const saved = xhr.status >= 200 && xhr.status < 300 && xhr.responseJSON?.status > 1;
          finish(saved ? null : new Error(text.failed(board + 1)));
        };
        const timer = setTimeout(() => finish(new Error(text.failed(board + 1))), 32000);
        events.on('ajaxComplete.verktygFrirond', complete);
        try {
          select.value = value;
          jQuery(select).trigger('chosen:updated');
          select.onchange();
        } catch {
          finish(new Error(text.failed(board + 1)));
        }
      });
    }
    alert(changes.length ? text.done(changes.length) : text.unchanged);
  } catch (e) {
    alert(text.error + e.message);
  } finally {
    window.verktygFrirondRunning = false;
  }
})();
```
