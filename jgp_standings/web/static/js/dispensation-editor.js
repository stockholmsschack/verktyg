import { html, nothing, render } from "./lit-html.js";
import { translate } from "./locale.js";
import { DispensationEditorState } from "./dispensation-editor-state.js";

function playerIdentity(dispensation) {
    return translate("dispensations.identity", {
        name: dispensation.name,
        birth_year: dispensation.birth_year,
        club: dispensation.club || translate("dispensations.no_club"),
    });
}

function dispensationTemplate(dispensation, requestRemoval, operation) {
    const disabled = operation !== null;
    return html`
        <li
            class="list-group-item"
            data-dispensation-id=${dispensation.player_id}
        >
            <div class="row g-2 align-items-center">
                <div class="col-sm-6">
                    <span class="fw-semibold">${dispensation.name}</span>
                    <span class="d-block small text-body-secondary">
                        ${translate("dispensations.club", {
                            club: dispensation.club
                                || translate("dispensations.no_club"),
                        })}
                    </span>
                </div>
                <div class="col-7 col-sm-4 text-sm-center">
                    ${translate("dispensations.plays_as_label")}
                    <span class="fw-semibold">
                        ${dispensation.dispensation_year}
                    </span>
                    <span class="text-nowrap">
                        (${translate("dispensations.born", {
                            birth_year: dispensation.birth_year,
                        })})
                    </span>
                </div>
                <div class="col-5 col-sm-2 text-end">
                    <button
                        class="btn btn-outline-danger btn-sm"
                        type="button"
                        aria-label=${translate("dispensations.remove_label", {
                            name: dispensation.name,
                        })}
                        ?disabled=${disabled}
                        title=${disabled ? translate("dispensations.saving") : ""}
                        @click=${function handleRemovalRequest() {
                            requestRemoval(dispensation);
                        }}
                    >
                        ${translate("dispensations.remove")}
                    </button>
                </div>
            </div>
        </li>
    `;
}

async function postJson(endpoint, body) {
    const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error("Dispensation request failed");
        error.messageKey = payload.error?.message_key;
        throw error;
    }
    return payload.data;
}

function toastMessage(outcome) {
    if (outcome.status === "added") {
        return translate("dispensations.added", {
            name: outcome.dispensation.name,
            year: outcome.dispensation.dispensation_year,
        });
    }
    if (outcome.status === "replaced") {
        return translate("dispensations.replaced", {
            name: outcome.dispensation.name,
            year: outcome.dispensation.dispensation_year,
            previous: outcome.previousYear,
        });
    }
    if (outcome.status === "removed") {
        return translate("dispensations.removed", {
            name: outcome.dispensations[0].name,
        });
    }
    return translate("dispensations.removed_all");
}

const root = document.querySelector("[data-dispensation-editor]");
if (root) {
    const exportButton = root.querySelector("[data-export-dispensations]");
    const exportStatus = root.querySelector("[data-dispensation-export-status]");
    let exporting = false;
    let exportError = null;
    const collection = root.querySelector("[data-dispensations]");
    const playerSelector = root.querySelector("[data-player-name-selector]");
    const yearInput = root.querySelector("[data-dispensation-year]");
    const saveButton = root.querySelector("[data-save-dispensation]");
    const saveStatus = root.querySelector("[data-dispensation-save-status]");
    const removeAllButton = root.querySelector(
        "[data-remove-all-dispensations]",
    );
    const countSummary = document.querySelector(
        "[data-configuration-dispensation-count]",
    );
    const removalDialogElement = root.querySelector(
        "[data-dispensation-removal-dialog]",
    );
    const removalHeading = root.querySelector(
        "[data-dispensation-removal-heading]",
    );
    const removalDescription = root.querySelector(
        "[data-dispensation-removal-description]",
    );
    const removalNames = root.querySelector(
        "[data-dispensation-removal-names]",
    );
    const confirmRemovalButton = root.querySelector(
        "[data-confirm-dispensation-removal]",
    );
    const toastElement = root.querySelector("[data-dispensation-toast]");
    const toastText = root.querySelector("[data-dispensation-toast-message]");
    const initialData = document.getElementById("dispensations-data");
    let configurationRevision = Number(root.dataset.configurationRevision);
    const oldestBandEnd = Number(root.dataset.oldestBandEnd);
    const removalDialog = new globalThis.bootstrap.Modal(removalDialogElement);
    const toast = new globalThis.bootstrap.Toast(toastElement, { delay: 8000 });
    let pendingRemoval = null;
    let operation = null;
    let outcomeMessageKey = null;
    let resettingSelector = false;

    function saveReason(state) {
        if (operation !== null) {
            return "dispensations.saving";
        }
        if (state.selectedPlayer === null) {
            return "dispensations.select_player";
        }
        if (state.selectedPlayer.birth_year <= oldestBandEnd) {
            return "dispensations.oldest_band";
        }
        if (editor.selectedYear() === null) {
            return "dispensations.enter_year";
        }
        return null;
    }

    function renderState(state) {
        let exportReason = exportError;
        if (exporting) {
            exportReason = "dispensations.exporting";
        } else if (operation !== null) {
            exportReason = "dispensations.saving";
        } else if (state.dispensations.length === 0) {
            exportReason = "dispensations.empty";
        }
        exportButton.disabled = exporting || operation !== null || state.dispensations.length === 0;
        exportStatus.textContent = exportReason ? translate(exportReason) : "";
        exportButton.title = exportStatus.textContent;
        const reason = saveReason(state);
        saveButton.disabled = reason !== null;
        saveButton.title = reason ? translate(reason) : "";
        const statusKeys = [outcomeMessageKey, reason].filter(
            (key, index, keys) => key !== null && keys.indexOf(key) === index,
        );
        saveStatus.textContent = statusKeys
            .map((key) => translate(key))
            .join(" ");
        saveStatus.dataset.saveState = operation === null ? "idle" : "saving";
        removeAllButton.disabled = (
            state.dispensations.length === 0 || operation !== null
        );
        removeAllButton.title = operation === null
            ? ""
            : translate("dispensations.saving");
        confirmRemovalButton.disabled = operation !== null;
        collection.dataset.dispensationCount = String(
            state.dispensations.length,
        );
        render(
            state.dispensations.length === 0
                ? html`<div class="alert alert-secondary mb-0">
                    ${translate("dispensations.empty")}
                </div>`
                : html`<ul class="list-group">
                    ${editor.sortedDispensations().map((dispensation) =>
                        dispensationTemplate(
                            dispensation,
                            requestOneRemoval,
                            operation,
                        ))}
                </ul>`,
            collection,
        );
        if (countSummary) {
            countSummary.textContent = state.dispensations.length === 0
                ? translate("configuration.none")
                : String(state.dispensations.length);
        }
    }

    const editor = new DispensationEditorState({
        initialDispensations: initialData
            ? JSON.parse(initialData.textContent)
            : [],
        onChange: renderState,
    });

    exportButton.addEventListener("click", async function exportPdf() {
        if (exportButton.disabled) return;
        exporting = true;
        exportError = null;
        renderState(editor.state);
        try {
            const url = new URL(root.dataset.exportEndpoint, window.location.href);
            url.searchParams.set("revision", configurationRevision);
            const response = await fetch(url);
            if (!response.ok) {
                const failure = await response.json();
                exportError = failure.error?.message_key || "dispensations.export_failed";
                return;
            }
            const blobUrl = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = blobUrl;
            link.download = response.headers.get("Content-Disposition")
                ?.match(/filename="?([^";]+)"?/)?.[1] || "Spelardispenser-JGP.pdf";
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        } catch {
            exportError = "dispensations.export_failed";
        } finally {
            exporting = false;
            renderState(editor.state);
        }
    });

    function requestOneRemoval(dispensation) {
        if (operation !== null) {
            return;
        }
        pendingRemoval = { kind: "one", playerId: dispensation.player_id };
        removalHeading.textContent = translate("dispensations.remove_heading");
        removalDescription.textContent = translate(
            "dispensations.remove_confirmation",
        );
        render(html`${playerIdentity(dispensation)}`, removalNames);
        confirmRemovalButton.textContent = translate("dispensations.remove");
        removalDialog.show();
    }

    function requestAllRemoval() {
        if (editor.state.dispensations.length === 0 || operation !== null) {
            return;
        }
        pendingRemoval = { kind: "all" };
        removalHeading.textContent = translate(
            "dispensations.remove_all_heading",
        );
        removalDescription.textContent = translate(
            "dispensations.remove_all_confirmation",
        );
        render(
            html`<ul class="mb-0">
                ${editor.sortedDispensations().map((dispensation) =>
                    html`<li>${playerIdentity(dispensation)}</li>`)}
            </ul>`,
            removalNames,
        );
        confirmRemovalButton.textContent = translate(
            "dispensations.remove_all",
        );
        removalDialog.show();
    }

    function resetSingleEntry() {
        resettingSelector = true;
        playerSelector.dispatchEvent(
            new CustomEvent("jgp:player-selector-reset"),
        );
        resettingSelector = false;
        yearInput.value = "";
        editor.setYear("");
    }

    async function persistMutation(mutate, afterSave = () => {}) {
        if (operation !== null) {
            return;
        }
        const previousDispensations = editor.draftValue();
        operation = "saving";
        outcomeMessageKey = "dispensations.saving";
        const outcome = mutate();
        if (outcome.status === "failure" || outcome.status === "unchanged") {
            operation = null;
            outcomeMessageKey = outcome.status === "unchanged"
                ? "dispensations.unchanged"
                : null;
            renderState(editor.state);
            return;
        }
        renderState(editor.state);
        try {
            const saved = await postJson(root.dataset.saveEndpoint, {
                revision: configurationRevision,
                dispensations: editor.value(),
            });
            configurationRevision = saved.revision;
            root.dataset.configurationRevision = String(saved.revision);
            editor.replaceDispensations(saved.dispensations);
            outcomeMessageKey = null;
            afterSave();
            toastText.textContent = toastMessage(outcome);
            toast.show();
            globalThis.dispatchEvent(new CustomEvent(
                "jgp:configuration-revision-changed",
                { detail: { revision: saved.revision, source: root } },
            ));
        } catch (error) {
            editor.replaceDispensations(previousDispensations);
            outcomeMessageKey = error?.messageKey ?? "errors.internal";
        } finally {
            operation = null;
            renderState(editor.state);
        }
    }

    root.addEventListener(
        "jgp:player-selection-changed",
        function selectPlayer(event) {
            if (!resettingSelector) {
                outcomeMessageKey = null;
            }
            if (event.detail.value === null) {
                editor.clearPlayerSelection();
                return;
            }
            editor.selectPlayer(event.detail.value);
            yearInput.focus();
        },
    );
    yearInput.addEventListener("input", function enterYear() {
        outcomeMessageKey = null;
        editor.setYear(yearInput.value);
    });
    saveButton.addEventListener("click", function saveDispensation() {
        if (saveReason(editor.state) !== null) {
            renderState(editor.state);
            return;
        }
        persistMutation(() => editor.addDispensation(), resetSingleEntry);
    });
    removeAllButton.addEventListener("click", requestAllRemoval);
    confirmRemovalButton.addEventListener("click", function confirmRemoval() {
        if (pendingRemoval === null || operation !== null) {
            return;
        }
        const removal = pendingRemoval;
        pendingRemoval = null;
        removalDialog.hide();
        persistMutation(
            () => removal.kind === "all"
                ? editor.removeAllDispensations()
                : editor.removeDispensation(removal.playerId),
        );
    });
    removalDialogElement.addEventListener(
        "hidden.bs.modal",
        function clearPendingRemoval() {
            pendingRemoval = null;
            render(nothing, removalNames);
        },
    );
    globalThis.addEventListener(
        "jgp:configuration-revision-changed",
        function adoptConfigurationRevision(event) {
            if (event.detail.source === root) {
                return;
            }
            configurationRevision = event.detail.revision;
            root.dataset.configurationRevision = String(configurationRevision);
        },
    );
    globalThis.addEventListener(
        "jgp:calendar-rollover-completed",
        function adoptCarriedDispensations(event) {
            editor.replaceDispensations(event.detail.dispensations);
        },
    );
    renderState(editor.state);
}
