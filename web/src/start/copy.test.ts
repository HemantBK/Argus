// The tagline has one home (copy.ts): no other source file in web/src may hold its text, and the absolute claim
// it replaced ("no finding rests on ...", CLAIMS_LEDGER N10) appears in no source file.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { TAGLINE } from "./copy";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const COPY = fileURLToPath(new URL("./copy.ts", import.meta.url));

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx|css|html)$/.test(name) ? [path] : [];
  });
}

const files = sources(SRC).filter((f) => f !== COPY && !f.endsWith("copy.test.ts"));

describe("TAGLINE", () => {
  it("is one sentence pair with no numbers", () => {
    expect(TAGLINE).toMatch(/^Every RBI rule an Argus finding rests on .+ or to a person\.$/);
    expect(TAGLINE).not.toMatch(/\d/);
  });

  it("is written out only in copy.ts", () => {
    expect(files.length).toBeGreaterThan(20);
    // Any 40-character run of the tagline counts, so a pasted copy cannot hide behind a small edit.
    const pieces = Array.from({ length: Math.floor(TAGLINE.length / 40) }, (_, i) => TAGLINE.slice(i * 40, i * 40 + 40));
    for (const f of files) {
      const text = readFileSync(f, "utf8").replace(/\s+/g, " ");
      for (const piece of pieces) expect(text.includes(piece), `${f} repeats the tagline: "${piece}"`).toBe(false);
    }
  });

  it("the old absolute claim is gone from web/src", () => {
    for (const f of files) expect(readFileSync(f, "utf8"), f).not.toMatch(/no finding rests on/i);
  });
});
