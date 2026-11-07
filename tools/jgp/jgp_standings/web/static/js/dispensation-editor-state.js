function validPlayer(player) {
    return (
        player !== null
        && typeof player === "object"
        && Number.isInteger(player.player_id)
        && player.player_id > 0
        && typeof player.first_name === "string"
        && player.first_name.trim() !== ""
        && typeof player.last_name === "string"
        && player.last_name.trim() !== ""
        && Number.isInteger(player.birth_year)
    );
}

export function dispensationYear(text) {
    const trimmed = String(text).trim();
    return /^[0-9]{4}$/.test(trimmed) && Number(trimmed) >= 1000
        ? Number(trimmed)
        : null;
}

function newDispensation(player, year) {
    const firstName = player.first_name.trim();
    const lastName = player.last_name.trim();
    return {
        player_id: player.player_id,
        name: `${firstName} ${lastName}`,
        birth_year: player.birth_year,
        club: player.club ?? player.current_club?.name ?? "",
        dispensation_year: year,
        first_name: firstName,
        last_name: lastName,
    };
}

function storedDispensations(values) {
    return values.map(function storedDispensation(value) {
        return {
            player_id: value.player_id,
            name: value.name,
            birth_year: value.birth_year,
            club: value.club,
            dispensation_year: value.dispensation_year,
            first_name: value.first_name ?? null,
            last_name: value.last_name ?? null,
        };
    });
}

export class DispensationEditorState {
    constructor({ initialDispensations = [], onChange = () => {} } = {}) {
        this.onChange = onChange;
        this.state = {
            selectedPlayer: null,
            yearText: "",
            dispensations: storedDispensations(initialDispensations),
        };
    }

    selectPlayer(player) {
        if (!validPlayer(player)) {
            return false;
        }
        this.#transition({ ...this.state, selectedPlayer: player });
        return true;
    }

    clearPlayerSelection() {
        this.#transition({ ...this.state, selectedPlayer: null });
    }

    setYear(text) {
        this.#transition({ ...this.state, yearText: String(text) });
    }

    selectedYear() {
        return dispensationYear(this.state.yearText);
    }

    existing(playerId) {
        return this.state.dispensations.find(
            function matchingPlayer(dispensation) {
                return dispensation.player_id === playerId;
            },
        );
    }

    addDispensation() {
        const player = this.state.selectedPlayer;
        const year = this.selectedYear();
        if (player === null || year === null) {
            return { status: "failure" };
        }
        const outcome = this.#merge([newDispensation(player, year)]);
        if (outcome.added.length === 1) {
            return { status: "added", dispensation: outcome.added[0] };
        }
        if (outcome.replaced.length === 1) {
            return {
                status: "replaced",
                dispensation: outcome.replaced[0].dispensation,
                previousYear: outcome.replaced[0].previousYear,
            };
        }
        return { status: "unchanged", dispensation: outcome.unchanged[0] };
    }

    removeDispensation(playerId) {
        const existing = this.existing(playerId);
        if (existing === undefined) {
            return { status: "failure" };
        }
        this.#transition({
            ...this.state,
            dispensations: this.state.dispensations.filter(
                function otherPlayer(dispensation) {
                    return dispensation.player_id !== playerId;
                },
            ),
        });
        return { status: "removed", dispensations: [existing] };
    }

    removeAllDispensations() {
        if (this.state.dispensations.length === 0) {
            return { status: "failure" };
        }
        const removed = this.state.dispensations;
        this.#transition({ ...this.state, dispensations: [] });
        return { status: "removed_all", dispensations: removed };
    }

    replaceDispensations(dispensations) {
        this.#transition({
            ...this.state,
            dispensations: storedDispensations(dispensations),
        });
    }

    sortedDispensations() {
        return this.state.dispensations.toSorted(function byName(left, right) {
            return left.name.localeCompare(right.name, "sv")
                || left.player_id - right.player_id;
        });
    }

    value() {
        return this.state.dispensations.map(function savedEntry(dispensation) {
            const entry = {
                player_id: dispensation.player_id,
                dispensation_year: dispensation.dispensation_year,
            };
            if (dispensation.first_name !== null) {
                entry.first_name = dispensation.first_name;
                entry.last_name = dispensation.last_name;
            }
            return entry;
        });
    }

    draftValue() {
        return storedDispensations(this.state.dispensations);
    }

    #merge(entries) {
        const added = [];
        const replaced = [];
        const unchanged = [];
        const dispensations = this.state.dispensations.slice();
        for (const entry of entries) {
            const index = dispensations.findIndex(
                function matchingPlayer(dispensation) {
                    return dispensation.player_id === entry.player_id;
                },
            );
            if (index === -1) {
                dispensations.push(entry);
                added.push(entry);
                continue;
            }
            const previous = dispensations[index];
            if (previous.dispensation_year === entry.dispensation_year) {
                unchanged.push(previous);
                continue;
            }
            const replacement = {
                ...previous,
                dispensation_year: entry.dispensation_year,
            };
            dispensations[index] = replacement;
            replaced.push({
                dispensation: replacement,
                previousYear: previous.dispensation_year,
            });
        }
        if (added.length > 0 || replaced.length > 0) {
            this.#transition({ ...this.state, dispensations });
        }
        return { added, replaced, unchanged };
    }

    #transition(state) {
        this.state = state;
        this.onChange(this.state);
    }
}
