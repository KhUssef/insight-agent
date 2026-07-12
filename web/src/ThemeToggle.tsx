import { useState } from "react";

// Three-state theme control: follow the system, or force light or dark.
// The choice is stamped on the root element as data-theme (which flips the
// CSS color-scheme, and with it every light-dark() token) and persisted in
// localStorage; an inline boot script in index.html restores it before
// first paint.

const STORAGE_KEY = "insight-theme";
const MODES = ["auto", "light", "dark"] as const;

type Mode = (typeof MODES)[number];

function currentMode(): Mode {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "auto";
}

function applyMode(mode: Mode): void {
  if (mode === "auto") {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem(STORAGE_KEY);
  } else {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem(STORAGE_KEY, mode);
  }
}

export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>(currentMode);
  const select = (next: Mode) => {
    applyMode(next);
    setMode(next);
  };
  return (
    <div className="theme-toggle" role="group" aria-label="Theme">
      {MODES.map((option) => (
        <button
          key={option}
          type="button"
          className={mode === option ? "is-active" : ""}
          aria-pressed={mode === option}
          onClick={() => select(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
