// Save only changes, and only once per submission.
const form = document.getElementById("access-form");
const save = document.getElementById("access-save");
const reason = document.getElementById("access-save-reason");
const fields = [...form.querySelectorAll("input[type=email]")];

// Browsers drop the spaces around commas in an email field's value.
const compact = (text) => text.replace(/\s/g, "");

function update() {
  const changed = fields.some(
    (field) => compact(field.value) !== compact(field.defaultValue),
  );
  save.disabled = !changed;
  reason.hidden = changed;
}

form.addEventListener("input", update);
form.addEventListener("submit", () => {
  save.disabled = true;
});
update();
