import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const moduleSource = await readFile(
    new URL(
        "../../jgp_standings/web/static/js/dispensation-editor-state.js",
        import.meta.url,
    ),
    "utf8",
);
const {
    dispensationYear,
    DispensationEditorState,
} = await import(
    `data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`
);

assert.equal(dispensationYear(" 2009 "), 2009);
assert.equal(dispensationYear("209"), null);
assert.equal(dispensationYear("２００９"), null);
assert.equal(dispensationYear("0999"), null);

const stored = {
    player_id: 546393,
    name: "Pratyush Tripathi",
    birth_year: 2010,
    club: "SS 4 Springare",
    dispensation_year: 2009,
};
const player = {
    player_id: 42,
    first_name: " My ",
    last_name: " Ek ",
    birth_year: 2014,
    current_club: { club_id: 10, name: "Wasa SK" },
};
const states = [];
const editor = new DispensationEditorState({
    initialDispensations: [stored],
    onChange(state) {
        states.push(state);
    },
});

assert.deepEqual(editor.value(), [
    { player_id: 546393, dispensation_year: 2009 },
]);
assert.equal(editor.addDispensation().status, "failure");
assert.equal(editor.selectPlayer({ player_id: "42", first_name: "My" }), false);
assert.equal(editor.selectPlayer(player), true);
assert.equal(editor.addDispensation().status, "failure");
editor.setYear("20");
assert.equal(editor.selectedYear(), null);
assert.equal(editor.addDispensation().status, "failure");
editor.setYear("2013");

const added = editor.addDispensation();
assert.equal(added.status, "added");
assert.deepEqual(added.dispensation, {
    player_id: 42,
    name: "My Ek",
    birth_year: 2014,
    club: "Wasa SK",
    dispensation_year: 2013,
    first_name: "My",
    last_name: "Ek",
});
assert.deepEqual(editor.value(), [
    { player_id: 546393, dispensation_year: 2009 },
    {
        player_id: 42,
        dispensation_year: 2013,
        first_name: "My",
        last_name: "Ek",
    },
]);
assert.deepEqual(
    editor.sortedDispensations().map((dispensation) => dispensation.name),
    ["My Ek", "Pratyush Tripathi"],
);

assert.equal(editor.addDispensation().status, "unchanged");
editor.setYear("2012");
const replaced = editor.addDispensation();
assert.equal(replaced.status, "replaced");
assert.equal(replaced.previousYear, 2013);
assert.equal(editor.state.dispensations.length, 2);

const beforeSave = editor.draftValue();
editor.replaceDispensations([
    stored,
    { ...stored, player_id: 42, name: "My Ek", dispensation_year: 2012 },
]);
assert.deepEqual(editor.value(), [
    { player_id: 546393, dispensation_year: 2009 },
    { player_id: 42, dispensation_year: 2012 },
]);
editor.replaceDispensations(beforeSave);
assert.equal(editor.value()[1].first_name, "My");

assert.equal(editor.selectPlayer({
    player_id: 84,
    first_name: "Bo",
    last_name: "Li",
    birth_year: 2013,
}), true);
editor.setYear("2012");
assert.equal(editor.addDispensation().status, "added");
assert.equal(editor.state.dispensations.at(-1).club, "");

assert.equal(editor.removeDispensation(999).status, "failure");
const removed = editor.removeDispensation(42);
assert.equal(removed.status, "removed");
assert.equal(removed.dispensations[0].name, "My Ek");

const removedAll = editor.removeAllDispensations();
assert.equal(removedAll.status, "removed_all");
assert.deepEqual(
    removedAll.dispensations.map((dispensation) => dispensation.player_id),
    [546393, 84],
);
assert.deepEqual(editor.value(), []);
assert.equal(editor.removeAllDispensations().status, "failure");
assert.ok(states.length > 0);

console.log("dispensation editor browser tests passed");
