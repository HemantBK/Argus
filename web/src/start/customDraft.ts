// The pasted clause last sent for review, kept in memory only, so "Edit your clause" after a refused or failed
// start brings it back instead of the sample text. Gone on reload; never written to browser storage.
let lastSent: string | null = null;

export function sentDraft(): string | null {
  return lastSent;
}

export function rememberSentDraft(text: string): void {
  lastSent = text;
}

/** Forget it (tests). */
export function forgetSentDraft(): void {
  lastSent = null;
}
