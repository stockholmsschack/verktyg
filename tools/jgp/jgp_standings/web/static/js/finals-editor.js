import { translate } from "./locale.js";

const DEFAULT_FIELD_COUNT = 3;

async function saveFinalsSeparators(endpoint, revision, separators) {
    const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision, finals_separators: separators }),
    });
    const payload = await response.json();
    if (!response.ok) {
        const error = new Error("Finals separator save failed");
        error.messageKey = payload.error?.message_key;
        throw error;
    }
    return payload.data;
}

function sameSeparators(left, right) {
    return (
        left.length === right.length
        && left.every((value, index) => value === right[index])
    );
}

const root = document.querySelector("[data-finals-editor]");
if (root) {
    const form = root.querySelector("[data-finals-form]");
    const groups = [...root.querySelectorAll("[data-finals-group]")];
    const clearAllGroupsButton = root.querySelector(
        "[data-clear-all-finals-groups]",
    );
    const saveButton = root.querySelector("[data-save-finals]");
    const status = root.querySelector("[data-finals-save-status]");
    const summary = document.querySelector(
        "[data-configuration-separator-summary]",
    );
    const dataElement = document.getElementById("finals-separators-data");
    let savedSeparators = dataElement
        ? JSON.parse(dataElement.textContent)
        : {};
    let configurationRevision = Number(root.dataset.configurationRevision);
    let saving = false;
    let outcomeMessageKey = null;
    let nextFieldId = 0;

    function groupList(group) {
        return group.querySelector("[data-finals-separator-list]");
    }

    function inputs(group) {
        return [...groupList(group).querySelectorAll("[data-finals-separator]")];
    }

    function allInputs() {
        return groups.flatMap(inputs);
    }

    function savedRows(group) {
        return savedSeparators[group.dataset.finalsGroup] ?? [];
    }

    function addField(group, value = "") {
        nextFieldId += 1;
        const id = `finals-separator-${nextFieldId}`;
        const row = document.createElement("div");
        row.className = "input-group input-group-sm";
        row.dataset.finalsSeparatorRow = "";

        const label = document.createElement("label");
        label.className = "input-group-text";
        label.htmlFor = id;
        label.textContent = translate("finals.row");

        const input = document.createElement("input");
        input.className = "form-control";
        input.id = id;
        input.type = "number";
        input.min = "1";
        input.step = "1";
        input.inputMode = "numeric";
        input.value = String(value);
        input.dataset.finalsSeparator = "";

        const clearButton = document.createElement("button");
        clearButton.className = "btn btn-outline-secondary";
        clearButton.type = "button";
        clearButton.textContent = translate("finals.clear");
        clearButton.setAttribute("aria-label", translate("finals.clear_row"));
        clearButton.dataset.clearFinalsSeparator = "";

        const removeButton = document.createElement("button");
        removeButton.className = "btn btn-outline-secondary";
        removeButton.type = "button";
        removeButton.textContent = "×";
        removeButton.setAttribute("aria-label", translate("finals.remove"));
        removeButton.title = translate("finals.remove");
        removeButton.dataset.removeFinalsSeparator = "";

        row.append(label, input, clearButton, removeButton);
        groupList(group).append(row);
        return input;
    }

    // Returns the group's sorted separators, or null when any field is invalid.
    function enteredSeparators(group) {
        const values = [];
        let valid = true;
        for (const input of inputs(group)) {
            const text = input.value.trim();
            const value = Number(text);
            const fieldValid = (
                input.validity.valid
                && (
                    text === ""
                    || (Number.isInteger(value) && value >= 1
                        && !values.includes(value))
                )
            );
            input.classList.toggle("is-invalid", !fieldValid);
            valid &&= fieldValid;
            if (fieldValid && text !== "") {
                values.push(value);
            }
        }
        return valid ? values.sort((left, right) => left - right) : null;
    }

    // Returns every group's separators keyed by label, leaving out empty
    // groups, or null when any group is invalid.
    function enteredGroups() {
        const entered = {};
        let valid = true;
        for (const group of groups) {
            const separators = enteredSeparators(group);
            valid &&= separators !== null;
            if (separators?.length) {
                entered[group.dataset.finalsGroup] = separators;
            }
        }
        return valid ? entered : null;
    }

    function groupsChanged(entered) {
        return groups.some(function groupChanged(group) {
            return !sameSeparators(
                entered[group.dataset.finalsGroup] ?? [],
                savedRows(group),
            );
        });
    }

    function renderClearButton(button, empty, emptyKey) {
        button.disabled = saving || empty;
        button.title = saving
            ? translate("finals.saving")
            : empty ? translate(emptyKey) : "";
    }

    function renderState() {
        const entered = enteredGroups();
        const changed = entered !== null && groupsChanged(entered);
        saveButton.disabled = saving || !changed;
        renderClearButton(
            clearAllGroupsButton,
            allInputs().every((input) => input.value === ""),
            "finals.all_groups_clear",
        );
        for (const group of groups) {
            for (const control of group.querySelectorAll("input, button")) {
                control.disabled = saving;
            }
            renderClearButton(
                group.querySelector("[data-clear-all-finals-separators]"),
                inputs(group).every((input) => input.value === ""),
                "finals.all_clear",
            );
            for (const input of inputs(group)) {
                renderClearButton(
                    input.parentElement.querySelector(
                        "[data-clear-finals-separator]",
                    ),
                    input.value === "",
                    "finals.already_clear",
                );
            }
        }
        if (saving) {
            status.textContent = translate("finals.saving");
        } else if (entered === null) {
            status.textContent = translate("finals.invalid");
        } else if (outcomeMessageKey !== null) {
            status.textContent = translate(outcomeMessageKey);
        } else if (!changed) {
            status.textContent = translate("finals.unchanged");
        } else {
            status.textContent = "";
        }
        saveButton.title = saveButton.disabled ? status.textContent : "";
    }

    function renderFields(group, separators) {
        groupList(group).replaceChildren();
        for (const value of separators) {
            addField(group, value);
        }
        for (let count = separators.length; count < DEFAULT_FIELD_COUNT; count += 1) {
            addField(group);
        }
    }

    function renderAllFields() {
        for (const group of groups) {
            renderFields(group, savedRows(group));
        }
    }

    function renderSummary() {
        if (!summary) {
            return;
        }
        const lines = groups
            .filter((group) => savedRows(group).length > 0)
            .map(function summaryLine(group) {
                const line = document.createElement("div");
                line.textContent = `${group.dataset.finalsHeading}: ${
                    savedRows(group).join(", ")
                }`;
                return line;
            });
        if (lines.length > 0) {
            summary.replaceChildren(...lines);
        } else {
            summary.textContent = translate("configuration.none");
        }
    }

    form.addEventListener("input", function handleInput() {
        outcomeMessageKey = null;
        renderState();
    });
    for (const group of groups) {
        const addButton = group.querySelector("[data-add-finals-separator]");
        groupList(group).addEventListener(
            "click",
            function clearOrRemoveSeparator(event) {
                const clearButton = event.target.closest(
                    "[data-clear-finals-separator]",
                );
                if (clearButton && !saving) {
                    const input = clearButton.parentElement.querySelector(
                        "[data-finals-separator]",
                    );
                    input.value = "";
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                    input.focus();
                    return;
                }
                const button = event.target.closest(
                    "[data-remove-finals-separator]",
                );
                if (!button || saving) {
                    return;
                }
                button.closest("[data-finals-separator-row]").remove();
                outcomeMessageKey = null;
                renderState();
                (inputs(group).at(-1) ?? addButton).focus();
            },
        );
        addButton.addEventListener("click", function addSeparator() {
            addField(group).focus();
            outcomeMessageKey = null;
            renderState();
        });
        group.querySelector("[data-clear-all-finals-separators]")
            .addEventListener("click", function clearGroup() {
                if (saving) {
                    return;
                }
                renderFields(group, []);
                outcomeMessageKey = null;
                renderState();
                inputs(group)[0]?.focus();
            });
    }
    clearAllGroupsButton.addEventListener("click", function clearAllGroups() {
        if (saving) {
            return;
        }
        for (const group of groups) {
            renderFields(group, []);
        }
        outcomeMessageKey = null;
        renderState();
        allInputs()[0]?.focus();
    });
    form.addEventListener("submit", async function handleSubmit(event) {
        event.preventDefault();
        const entered = enteredGroups();
        if (saving || entered === null || !groupsChanged(entered)) {
            renderState();
            return;
        }
        saving = true;
        renderState();
        try {
            const saved = await saveFinalsSeparators(
                root.dataset.saveEndpoint,
                configurationRevision,
                entered,
            );
            configurationRevision = saved.revision;
            root.dataset.configurationRevision = String(saved.revision);
            savedSeparators = saved.finals_separators;
            renderAllFields();
            outcomeMessageKey = "finals.saved";
            renderSummary();
            globalThis.dispatchEvent(new CustomEvent(
                "jgp:configuration-revision-changed",
                {
                    detail: {
                        revision: saved.revision,
                        source: root,
                    },
                },
            ));
        } catch (error) {
            outcomeMessageKey = error?.messageKey ?? "errors.internal";
        } finally {
            saving = false;
            renderState();
        }
    });
    globalThis.addEventListener(
        "jgp:configuration-revision-changed",
        function adoptConfigurationRevision(event) {
            if (event.detail.source === root) {
                return;
            }
            configurationRevision = event.detail.revision;
            root.dataset.configurationRevision = String(
                configurationRevision,
            );
        },
    );
    globalThis.addEventListener(
        "jgp:calendar-rollover-completed",
        function adoptRolloverReset() {
            savedSeparators = {};
            renderAllFields();
            outcomeMessageKey = null;
            renderSummary();
            renderState();
        },
    );
    renderAllFields();
    renderState();
}
