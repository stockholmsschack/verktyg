// Light, dark or automatic colour mode, adapted from Bootstrap 5.3's docs color-modes.js (MIT).
// Loaded in <head> without defer, so the page never paints in the wrong mode.
// "auto" follows the operating system and is the default; a choice is kept per browser.
(() => {
  "use strict";

  const storageKey = "verktyg-theme";
  const dark = window.matchMedia("(prefers-color-scheme: dark)");

  const storedTheme = () => {
    try {
      return localStorage.getItem(storageKey);
    } catch {
      return null;
    }
  };

  const storeTheme = (theme) => {
    try {
      localStorage.setItem(storageKey, theme);
    } catch {
      // Private windows and blocked storage only lose the remembered choice.
    }
  };

  const preferredTheme = () => {
    const stored = storedTheme();
    return stored === "light" || stored === "dark" || stored === "auto" ? stored : "auto";
  };

  const applyTheme = (theme) => {
    const resolved = theme === "auto" ? (dark.matches ? "dark" : "light") : theme;
    document.documentElement.setAttribute("data-bs-theme", resolved);
  };

  const showActiveTheme = (theme) => {
    const switcher = document.querySelector("#theme-choice");
    if (!switcher) {
      return;
    }
    const active = document.querySelector(`[data-bs-theme-value="${theme}"]`);
    document.querySelectorAll("[data-bs-theme-value]").forEach((element) => {
      const isActive = element === active;
      element.classList.toggle("active", isActive);
      element.setAttribute("aria-pressed", String(isActive));
    });
    switcher.querySelector("use").setAttribute("href", active.querySelector("use").getAttribute("href"));
    const label = switcher.dataset.label;
    switcher.setAttribute("aria-label", `${label} (${active.textContent.trim()})`);
    switcher.setAttribute("title", switcher.getAttribute("aria-label"));
  };

  applyTheme(preferredTheme());

  dark.addEventListener("change", () => {
    if (preferredTheme() === "auto") {
      applyTheme("auto");
    }
  });

  window.addEventListener("DOMContentLoaded", () => {
    showActiveTheme(preferredTheme());
    document.querySelectorAll("[data-bs-theme-value]").forEach((toggle) => {
      toggle.addEventListener("click", () => {
        const theme = toggle.getAttribute("data-bs-theme-value");
        storeTheme(theme);
        applyTheme(theme);
        showActiveTheme(theme);
      });
    });
  });
})();
