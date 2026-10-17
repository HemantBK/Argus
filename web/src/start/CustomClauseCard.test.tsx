// @vitest-environment jsdom
// "Try your own clause" (D-60 track C): the card's validation display, the request it sends, how the API's
// refusals are read, and the plain-English banner for every refusal code and limit. Limits come from the
// production constants; the prefill from the bundled sample file.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomStartProblemBanner } from "../shell/banners";
import { href } from "../shell/route";
import { live } from "../shell/screens.test-util";
import { run } from "../data/fixtures.test-util";
import { savedRunOfferText } from "../shell/busy";
import { ATTEST_LABEL, CUSTOM_CLOSED_NOTE, CUSTOM_NOTICE, CustomClauseCard } from "./CustomClauseCard";
import { forgetSentDraft } from "./customDraft";
import { CUSTOM_PREFILL, CUSTOM_PREFILL_SOURCE, sampleClauseText } from "./samplePolicies";
import { StartScreen } from "./StartScreen";

vi.mock("../firebase", () => ({ getIdToken: vi.fn(() => Promise.resolve("FAKE-TOKEN")) }));
vi.mock("../env", () => ({ appEnv: () => ({ apiBase: "http://api.test" }) }));

const {
  ApiError,
  CUSTOM_CLAUSE_MAX_CHARS,
  CUSTOM_CLAUSE_MIN_CHARS,
  CUSTOM_POLICY_ID,
  CUSTOM_REFUSAL_CODES,
  clauseLength,
  newRunId,
  normaliseClause,
  startCustomRun,
  startRun,
} = await import("../data/api");

afterEach(() => {
  cleanup();
  forgetSentDraft();
  vi.unstubAllGlobals();
});

const FAKE_CLAUSE = "FAKE clause: the borrower may cancel the FAKE loan within three days of disbursal.";
const fmt = (n: number): string => n.toLocaleString("en-IN");

function respond(status: number, body: unknown): void {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status }))));
}

async function refusedWith(status: number, body: unknown): Promise<InstanceType<typeof ApiError>> {
  respond(status, body);
  const err = await startCustomRun(newRunId(), { clauseText: FAKE_CLAUSE, attest: true }).catch((e: unknown) => e);
  if (!(err instanceof ApiError)) throw new Error("expected an ApiError");
  return err;
}

function renderCard(prefill = FAKE_CLAUSE) {
  const onRun = vi.fn();
  render(<CustomClauseCard prefill={prefill} onRun={onRun} open />);
  const box = screen.getByRole("textbox", { name: "One clause of a digital-lending policy" });
  const tick = screen.getByRole("checkbox", { name: ATTEST_LABEL });
  const review = screen.getByRole("button", { name: "Review" });
  return { onRun, box, tick, review };
}

describe("the prefill", () => {
  it("is the bundled sample clause it names, read from the same file the API loads", () => {
    const file = JSON.parse(readFileSync(resolve(process.cwd(), `../backend/argus_api/samples/${CUSTOM_PREFILL_SOURCE.policyId}.json`), "utf8")) as {
      clauses: { id: string; text: string }[];
    };
    const expected = file.clauses.find((c) => c.id === CUSTOM_PREFILL_SOURCE.clauseId)?.text;
    expect(expected).toBeTruthy();
    expect(CUSTOM_PREFILL).toBe(expected);
    const length = clauseLength(CUSTOM_PREFILL);
    expect(length).toBeGreaterThanOrEqual(CUSTOM_CLAUSE_MIN_CHARS);
    expect(length).toBeLessThanOrEqual(CUSTOM_CLAUSE_MAX_CHARS);
  });
  it("is null for a missing policy or clause", () => {
    const files = { "x/policy_z.json": { policy_id: "policy_z", clauses: [{ id: "c1", text: "FAKE" }] } };
    expect(sampleClauseText(files, "policy_z", "c1")).toBe("FAKE");
    expect(sampleClauseText(files, "policy_z", "c9")).toBeNull();
    expect(sampleClauseText(files, "policy_y", "c1")).toBeNull();
  });
});

describe("clauseLength", () => {
  it("counts after NFC normalisation, in code points, without control characters or surrounding space", () => {
    expect(normaliseClause("é")).toBe("é");
    expect(clauseLength("é")).toBe(1);
    expect(clauseLength("\u{1F600}")).toBe(1);
    expect(clauseLength("a\u0000b\u0007c\u007f")).toBe(3);
    expect(clauseLength("  a\nb\tc  ")).toBe(5);
    expect(clauseLength("a\r\nb\rc")).toBe(5);
    expect(normaliseClause("ABCDE\u200b1234F")).toBe("ABCDE1234F");
  });
});

describe("CustomClauseCard", () => {
  it("shows the notice, the prefilled clause and its live character count", () => {
    renderCard();
    expect(screen.getByText(CUSTOM_NOTICE)).toBeTruthy();
    expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe(FAKE_CLAUSE);
    expect(
      screen.getByText(`${fmt(clauseLength(FAKE_CLAUSE))} of ${fmt(CUSTOM_CLAUSE_MIN_CHARS)}–${fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters`),
    ).toBeTruthy();
  });

  it("needs the attestation before Review is enabled", () => {
    const { onRun, tick, review } = renderCard();
    expect((review as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Tick the box to confirm the text contains no personal data.")).toBeTruthy();
    fireEvent.click(review);
    expect(onRun).not.toHaveBeenCalled();
    fireEvent.click(tick);
    expect((review as HTMLButtonElement).disabled).toBe(false);
  });

  it("says when the clause is too short, at the boundary the API uses", () => {
    const { box, tick, review } = renderCard();
    fireEvent.click(tick);
    fireEvent.change(box, { target: { value: "x".repeat(CUSTOM_CLAUSE_MIN_CHARS - 1) } });
    expect(screen.getByText(`Too short: a clause needs at least ${fmt(CUSTOM_CLAUSE_MIN_CHARS)} characters.`)).toBeTruthy();
    expect((review as HTMLButtonElement).disabled).toBe(true);
    expect(box.getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(box, { target: { value: "x".repeat(CUSTOM_CLAUSE_MIN_CHARS) } });
    expect((review as HTMLButtonElement).disabled).toBe(false);
    expect(box.getAttribute("aria-invalid")).toBe("false");
  });

  it("says when the clause is too long, and by how much", () => {
    const { box, tick, review } = renderCard();
    fireEvent.click(tick);
    fireEvent.change(box, { target: { value: "x".repeat(CUSTOM_CLAUSE_MAX_CHARS + 7) } });
    expect(screen.getByText(`Too long: one clause of at most ${fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters. Remove 7.`)).toBeTruthy();
    expect((review as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box, { target: { value: "x".repeat(CUSTOM_CLAUSE_MAX_CHARS) } });
    expect((review as HTMLButtonElement).disabled).toBe(false);
  });

  it("starts a custom run with the text as typed and the attestation, and keeps that text for Edit your clause", () => {
    const { onRun, box, tick, review } = renderCard();
    const edited = `${FAKE_CLAUSE} FAKE edit.`;
    fireEvent.change(box, { target: { value: edited } });
    fireEvent.click(tick);
    fireEvent.click(review);
    expect(onRun).toHaveBeenCalledWith(CUSTOM_POLICY_ID, { clauseText: edited, attest: true });
    cleanup();
    const again = renderCard();
    expect((again.box as HTMLTextAreaElement).value).toBe(edited);
    expect((again.tick as HTMLInputElement).checked).toBe(false);
  });

  it("sits on the Start screen as its own card and starts through the screen's onRun", () => {
    const onRun = vi.fn();
    render(<StartScreen saved={live.ready({ runs: [], invalid: [] })} onRun={onRun} policies={{ policies: [], invalid: [] }} prefill={FAKE_CLAUSE} customOpen />);
    const card = within(screen.getByRole("region", { name: "Try your own clause" }));
    fireEvent.click(card.getByRole("checkbox", { name: ATTEST_LABEL }));
    fireEvent.click(card.getByRole("button", { name: "Review" }));
    expect(onRun).toHaveBeenCalledWith(CUSTOM_POLICY_ID, { clauseText: FAKE_CLAUSE, attest: true });
  });

  it("closed (the default, as on a deployed build): says so plainly and offers no form to send", () => {
    const onRun = vi.fn();
    render(<StartScreen saved={live.ready({ runs: [], invalid: [] })} onRun={onRun} policies={{ policies: [], invalid: [] }} prefill={FAKE_CLAUSE} />);
    const region = screen.getByRole("region", { name: "Try your own clause" });
    expect(within(region).getByText(CUSTOM_CLOSED_NOTE)).toBeTruthy();
    expect(within(region).queryByRole("textbox")).toBeNull();
    expect(within(region).queryByRole("button")).toBeNull();
    expect(region.textContent).not.toContain(CUSTOM_NOTICE);
  });
});

describe("startCustomRun", () => {
  it("posts run_id, policy_id custom, clause_text and attest", async () => {
    const id = newRunId();
    respond(200, { run_id: id, status: "done" });
    await expect(startCustomRun(id, { clauseText: FAKE_CLAUSE, attest: true })).resolves.toEqual({ run_id: id, status: "done" });
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(JSON.parse(init?.body as string)).toEqual({ run_id: id, policy_id: CUSTOM_POLICY_ID, clause_text: FAKE_CLAUSE, attest: true });
  });

  it("refuses a custom run without its text before calling the API", async () => {
    respond(200, {});
    await expect(startRun(newRunId(), CUSTOM_POLICY_ID)).rejects.toBeInstanceOf(ApiError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(CUSTOM_REFUSAL_CODES)("reads a structured %s refusal into ApiError.refusal, never with the clause text", async (code) => {
    const err = await refusedWith(400, { detail: { code, message: `FAKE server message for ${code}`, ...(code === "identifier_found" ? { identifier_type: "PAN" } : {}) } });
    expect(err).toMatchObject({ kind: "http", status: 400, refusal: { code } });
    expect(err.refusal?.identifierType).toBe(code === "identifier_found" ? "PAN" : undefined);
    expect(err.message).toContain(`FAKE server message for ${code}`);
    expect(err.message).not.toContain(FAKE_CLAUSE);
  });

  it("keeps unknown structured codes and plain string details as ordinary HTTP errors", async () => {
    expect((await refusedWith(400, { detail: { code: "FAKE_unknown", message: "FAKE" } })).refusal).toBeUndefined();
    const off = await refusedWith(503, { detail: "FAKE switched off" });
    expect(off).toMatchObject({ status: 503, detail: "FAKE switched off", refusal: undefined });
  });
});

describe("CustomStartProblemBanner", () => {
  const banner = (error: Error) => {
    const onRetry = vi.fn();
    render(<CustomStartProblemBanner error={error} offers={OFFERS} onRetry={onRetry} />);
    const alert = within(screen.getByRole("alert"));
    return { onRetry, alert, title: alert.getByRole("strong").textContent, text: screen.getByRole("alert").textContent };
  };
  const OFFERS = [
    run({ run_id: "fake-saved-a", saved: true, policy_title: "FAKE FinFast NBFC (Synthetic)" }),
    run({ run_id: "fake-saved-b", saved: true, policy_id: "policy_b", policy_title: "FAKE LendRight NBFC (Synthetic)" }),
  ];
  const refusal = (code: (typeof CUSTOM_REFUSAL_CODES)[number], identifierType?: string) =>
    new ApiError("http", "FAKE", 400, "FAKE server message", { code, ...(identifierType ? { identifierType } : {}) });

  const CASES: { name: string; error: () => Error; title: RegExp; body: RegExp; retry: boolean }[] = [
    { name: "too_short", error: () => refusal("too_short"), title: /too short/, body: new RegExp(`at least ${fmt(CUSTOM_CLAUSE_MIN_CHARS)} characters`), retry: false },
    { name: "too_long", error: () => refusal("too_long"), title: /too long/, body: new RegExp(`at most ${fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters`), retry: false },
    { name: "not_attested", error: () => refusal("not_attested"), title: /confirm the text contains no personal data/, body: /Tick the box/, retry: false },
    { name: "identifier_found (PAN)", error: () => refusal("identifier_found", "PAN"), title: /seems to contain a PAN\.$/, body: /was not reviewed/, retry: false },
    { name: "identifier_found (unknown type)", error: () => refusal("identifier_found", "FAKE_TYPE"), title: /personal identifier \(FAKE_TYPE\)/, body: /placeholder/, retry: false },
    { name: "identifier_found (no type)", error: () => refusal("identifier_found"), title: /seems to contain a personal identifier\.$/, body: /placeholder/, retry: false },
    // Limits and outages: the API's sentences at the deployed build (runs.py), said in our words, never as sent.
    {
      name: "429 cap",
      error: () => new ApiError("http", "FAKE", 429, "Today's limit of 5 custom clause reviews is used up. Try a sample policy, or paste a clause again tomorrow (India time)."),
      title: /limit has been reached/,
      body: /^[^.]*limit has been reached for now\.Today's limit of 5 pasted-clause reviews is used up\. Try a sample policy, or paste a clause again tomorrow \(India time\)\.Edit your clause/,
      retry: true,
    },
    {
      name: "429 daily cap",
      error: () => new ApiError("http", "FAKE", 429, "Today's limit of 100 live runs is used up. Open a saved run, or try again tomorrow (India time)."),
      title: /limit has been reached/,
      body: /for now\.Today's limit of 100 live runs is used up\. Try again tomorrow \(India time\)\.Edit your clause/,
      retry: true,
    },
    {
      name: "429 unknown reason",
      error: () => new ApiError("http", "FAKE", 429, "FAKE cap reached"),
      title: /limit has been reached/,
      body: /for now\.The live run limit has been reached for now\. Try again later\.Edit your clause/,
      retry: true,
    },
    {
      name: "503 switched off",
      error: () => new ApiError("http", "FAKE", 503, "Custom clause review is switched off right now."),
      title: /not available right now/,
      body: /right now\.Pasting your own clause is switched off right now\. The sample policies and their saved runs are still on the start screen\./,
      retry: true,
    },
    {
      name: "503 limits missing",
      error: () => new ApiError("http", "FAKE", 503, "Run limits are not configured: config/limits is missing"),
      title: /not available right now/,
      body: /right now\.Live runs are not set up on the server right now\. The sample policies/,
      retry: true,
    },
    { name: "network", error: () => new ApiError("network", "FAKE unreachable"), title: /could not reach/, body: /FAKE unreachable/, retry: true },
    { name: "listener refused", error: () => new Error("FAKE permission denied"), title: /could not watch it/, body: /FAKE permission denied/, retry: true },
    { name: "other 4xx", error: () => new ApiError("http", "FAKE refused", 422), title: /could not be started/, body: /FAKE refused/, retry: false },
  ];

  it("names every identifier type the API's prescan reports in plain English", () => {
    const source = readFileSync(resolve(process.cwd(), "../backend/argus_api/custom.py"), "utf8");
    const types = [...source.matchAll(/\(\s*"([^"]+)",\s*re\.compile/g)].map((m) => m[1] ?? "");
    expect(types.length).toBeGreaterThan(0);
    for (const type of types) {
      const b = banner(refusal("identifier_found", type));
      expect(b.title).toMatch(/^Your clause seems to contain (a|an) [A-Za-z -]+\.$/);
      expect(b.title).not.toContain("personal identifier");
      cleanup();
    }
  });

  it.each(CASES)("$name: plain English, a way back to the clause, and Try again only when it could help", ({ name, error, title, body, retry }) => {
    const b = banner(error());
    expect(b.title).toMatch(title);
    expect(b.text).toMatch(body);
    expect(b.alert.getByRole("link", { name: "Edit your clause" }).getAttribute("href")).toBe(href.start());
    // The limit reached or review switched off: the sample policies' saved runs are offered too; never otherwise.
    const offered = b.alert.queryAllByRole("link").filter((a) => a.textContent !== "Edit your clause");
    expect(offered.map((a) => a.textContent)).toEqual(/^(429|503) /.test(name) ? OFFERS.map(savedRunOfferText) : []);
    // Never the API's raw sentence: no internal path, no code, no "FAKE" detail.
    expect(b.text).not.toMatch(/config\/|FAKE cap reached|custom clause reviews|Open a saved run, or/);
    const button = b.alert.queryByRole("button", { name: "Try again" });
    expect(button !== null).toBe(retry);
    if (button) {
      fireEvent.click(button);
      expect(b.onRetry).toHaveBeenCalledTimes(1);
    }
  });
});
