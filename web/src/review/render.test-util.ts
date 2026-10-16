// Server-side rendering for component tests: the test environment is node (no DOM library is installed), so
// components are rendered to static HTML and asserted on their markup and visible text.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

export function html(node: ReactNode): string {
  return renderToStaticMarkup(node);
}

const ENTITIES: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'", "&#39;": "'" };

/** Visible text of rendered markup: tags removed, entities decoded, whitespace collapsed. */
export function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, " ")
    .replace(/&(amp|lt|gt|quot|#x27|#39);/g, (m) => ENTITIES[m] ?? m)
    .replace(/\s+/g, " ")
    .trim();
}

/** Number of non-overlapping occurrences of `needle` in `haystack`. */
export function count(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}
