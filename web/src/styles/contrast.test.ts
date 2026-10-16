import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// UI_SPEC 4.2: every text/background pair reaches 4.5:1. Values are read from tokens.css itself.
const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");
const tokens = new Map([...css.matchAll(/--([a-z-]+):\s*(#[0-9A-Fa-f]{6})/g)].map((m) => [m[1] ?? "", m[2] ?? ""]));

function token(name: string): string {
  const v = tokens.get(name);
  if (!v) throw new Error(`token --${name} missing from tokens.css`);
  return v;
}

function luminance(hex: string): number {
  const ch = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = ch.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("tokens.css contrast", () => {
  const statuses = ["ok", "gap", "outdated", "review"];
  it.each(statuses)("status-%s text on its tint and on the page reaches 4.5:1", (s) => {
    expect(contrast(token(`status-${s}`), token(`status-${s}-bg`))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(`status-${s}`), token("bg"))).toBeGreaterThanOrEqual(4.5);
  });
  it.each(["text", "text-muted", "accent"])("%s on bg and surface reaches 4.5:1", (t) => {
    expect(contrast(token(t), token("bg"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(t), token("surface"))).toBeGreaterThanOrEqual(4.5);
  });
  it("white text on the accent button reaches 4.5:1", () => {
    expect(contrast("#FFFFFF", token("accent"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast("#FFFFFF", token("accent-hover"))).toBeGreaterThanOrEqual(4.5);
  });
});
