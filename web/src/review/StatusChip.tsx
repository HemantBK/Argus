// Finding type chip (UI_SPEC 4.1): icon + text label + colour, never colour alone.
import type { ReactNode } from "react";
import type { Chip } from "../data/selectors";

export function StatusChip({ chip, compact = false }: { chip: Chip; compact?: boolean }): ReactNode {
  return (
    <span className={`chip chip-${chip.tone}${compact ? " chip-compact" : ""}`}>
      <span className="chip-icon" aria-hidden="true">
        {chip.icon}
      </span>
      <span>{chip.label}</span>
    </span>
  );
}

export function Spinner({ label }: { label: string }): ReactNode {
  return (
    <span className="spinner-wrap">
      <span className="spinner" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </span>
  );
}
