// firebase.json: the files that say which build is live must never be served from a cache. Without no-cache,
// Hosting sends max-age=3600 for "/", "/index.html" and "/version.json" (read from argus-aibc.web.app on
// 18 Oct 2026), so a browser or the deploy check could see the previous build for up to an hour.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

interface HeaderRule {
  source: string;
  headers: { key: string; value: string }[];
}

const config = JSON.parse(readFileSync(fileURLToPath(new URL("../../../firebase.json", import.meta.url)), "utf8")) as {
  hosting: { public: string; headers?: HeaderRule[]; rewrites?: unknown[] };
};

describe("firebase.json hosting headers", () => {
  it.each(["/", "/index.html", "/version.json"])("%s is served with Cache-Control: no-cache", (path) => {
    const rules = (config.hosting.headers ?? []).filter((r) => r.source === path);
    const values = rules.flatMap((r) => r.headers.filter((h) => h.key.toLowerCase() === "cache-control").map((h) => h.value));
    expect(values).toEqual(["no-cache"]);
  });

  it("serves the web build the stamp is written into, and keeps the single-page rewrite", () => {
    expect(config.hosting.public).toBe("web/dist");
    expect(config.hosting.rewrites).toEqual([{ source: "**", destination: "/index.html" }]);
  });
});
