// @vitest-environment jsdom
// "Print evidence report": the button appears only when the whole run can be printed, the report is in the page
// only while the browser prints (the button or the browser's own print command), and it holds what report.ts built.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { run } from "../data/fixtures.test-util";
import type { RunDoc } from "../data/types";
import { auditEvent } from "../shell/screens.test-util";
import { EvidenceReport } from "./EvidenceReport";
import { CLAUSES, CURRENT_CITATION, LOADING, OLD_REF, findings, noSignoff, ready, sampleRun, steps } from "./fixtures.test-util";
import { REPORT_TITLE, REPORT_TITLE_SYNTHETIC, evidenceReport } from "./report";
import { PRINT_BUTTON, ReviewWorkspace, type ReviewWorkspaceProps } from "./ReviewWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const AUDIT = [auditEvent(1, { event: "run_started" }), auditEvent(2)];

function workspace(over: Partial<ReviewWorkspaceProps> = {}) {
  return render(
    <ReviewWorkspace
      source="samples"
      run={ready<RunDoc | null>(sampleRun())}
      steps={ready(steps())}
      findings={ready(findings())}
      audit={ready(AUDIT)}
      synthetic
      onSignoff={noSignoff}
      {...over}
    />,
  );
}

const report = (): Element | null => document.querySelector(".evidence-report");

describe("Print evidence report", () => {
  it("prints the report through window.print, and the report leaves the page once printing ends", () => {
    let duringPrint: string | null = null;
    const print = vi.fn(() => {
      act(() => {
        window.dispatchEvent(new Event("beforeprint"));
      });
      duringPrint = report()?.textContent ?? null;
      act(() => {
        window.dispatchEvent(new Event("afterprint"));
      });
    });
    vi.stubGlobal("print", print);
    workspace();
    expect(report()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: PRINT_BUTTON }));
    expect(print).toHaveBeenCalledTimes(1);
    expect(duringPrint).toContain(REPORT_TITLE_SYNTHETIC);
    expect(duringPrint).toContain("Audit summary");
    expect(report()).toBeNull();
  });

  it("the browser's own print command prints the same report", () => {
    workspace({ synthetic: false });
    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });
    expect(screen.getByRole("heading", { level: 1, name: REPORT_TITLE })).toBeTruthy();
    act(() => {
      window.dispatchEvent(new Event("afterprint"));
    });
    expect(report()).toBeNull();
  });

  it.each<[string, Partial<ReviewWorkspaceProps>]>([
    ["the run is still running", { run: ready<RunDoc | null>(run({ status: "running", clauses: CLAUSES })) }],
    ["the findings are loading", { findings: LOADING }],
    ["the trace is loading", { steps: LOADING }],
    ["the audit log is loading", { audit: LOADING }],
    ["the audit log is not given", { audit: undefined }],
  ])("is not offered while %s, and printing then leaves the screen as it is", (_, over) => {
    workspace(over);
    expect(screen.queryByRole("button", { name: PRINT_BUTTON })).toBeNull();
    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });
    expect(report()).toBeNull();
  });
});

describe("EvidenceReport markup", () => {
  it("prints each clause with its citation, the withdrawn chain, the monitor, the decision, then the audit summary", () => {
    const r = evidenceReport({ run: sampleRun(), findings: findings(), steps: steps(), audit: AUDIT, synthetic: true, printedAt: new Date() });
    render(<EvidenceReport report={r} />);
    const text = report()?.textContent ?? "";
    for (const c of CLAUSES) expect(text).toContain(c.text);
    expect(text).toContain(CURRENT_CITATION.quote);
    expect(text).toContain(CURRENT_CITATION.url);
    expect(text).toContain(OLD_REF);
    expect(text).toContain("Officer's decision:");
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent).at(-1)).toBe("Audit summary");
    for (const line of r.summary.lines) expect(text).toContain(line);
  });

  it("labels a finding the backup model wrote", () => {
    const fs = findings().map((f) => (f.clause_id === "c2" ? { ...f, agent_model: "gemini-3.1-flash-lite", fallback_reason: "FAKE busy" } : f));
    const r = evidenceReport({ run: sampleRun({ agent_model: "gemini-3.8-flash" }), findings: fs, steps: steps(), audit: AUDIT, synthetic: true, printedAt: new Date() });
    render(<EvidenceReport report={r} />);
    const backup = screen.getAllByText(/\(backup model\)/);
    expect(backup).toHaveLength(1);
    expect(backup[0]?.tagName).toBe("STRONG");
    expect(backup[0]?.textContent).toBe("Gemini 3.1 Flash-Lite (backup model), because FAKE busy");
  });
});
