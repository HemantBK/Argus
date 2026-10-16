import { describe, expect, it } from "vitest";
import { finding, T0 } from "../data/fixtures.test-util";
import { actorLabel } from "../data/selectors";
import { STEP_KIND_VALUES } from "../generated/schema";
import { auditEvent } from "../shell/screens.test-util";
import { actionLabel, actorKind, auditRows } from "./auditRows";

describe("actionLabel", () => {
  it("has plain English for every step kind and the known events", () => {
    for (const kind of STEP_KIND_VALUES) expect(actionLabel(`step_${kind}`)).not.toMatch(/_/);
    for (const e of ["run_started", "run_finished", "finding_recorded", "clause_failed", "signoff_accept", "signoff_reject", "signoff_edit"]) {
      expect(actionLabel(e)).not.toMatch(/_/);
    }
  });
  it("shows an unknown event readably instead of dropping it", () => {
    expect(actionLabel("fake_new_event")).toBe("Fake new event");
    expect(actionLabel("")).toBe("(no action recorded)");
  });
});

describe("actorKind", () => {
  it("recognises officers by their uid prefix", () => {
    expect(actorKind("officer:FAKE-uid")).toBe("officer");
    expect(actorKind("monitor")).toBe("monitor");
    expect(actorKind("FAKE-system")).toBe("other");
  });
});

describe("auditRows", () => {
  it("orders by seq and keeps every event", () => {
    const rows = auditRows([auditEvent(3), auditEvent(1), auditEvent(2)], [], []);
    expect(rows.map((r) => r.seq)).toEqual([1, 2, 3]);
  });
  it("labels actors, clause titles and models from the record", () => {
    const [row] = auditRows(
      [auditEvent(1, { actor: "monitor", event: "step_monitor", clause_id: "c4", model: "FAKE-monitor-model" })],
      [],
      [{ clause_id: "c4", title: "FAKE clause title", text: "FAKE" }],
    );
    expect(row).toMatchObject({ actor: actorLabel("monitor"), clauseId: "c4", clauseTitle: "FAKE clause title", model: "FAKE-monitor-model" });
  });
  it("shows before/after for an officer edit from the recorded finding", () => {
    const f = finding({
      clause_id: "c2",
      rationale: "FAKE agent rationale",
      signoff: { decision: "edit", by: "FAKE-uid", at: T0, note: "", edited_rationale: "FAKE officer rationale" },
    });
    const rows = auditRows(
      [auditEvent(1, { actor: "officer:FAKE-uid", event: "signoff_edit", clause_id: "c2" }), auditEvent(2, { clause_id: "c2" })],
      [f],
      [],
    );
    expect(rows[0]?.edit).toEqual({ before: f.rationale, after: f.signoff?.edited_rationale });
    expect(rows[1]?.edit).toBeNull();
  });
  it("has no before/after for an accept", () => {
    const f = finding({ clause_id: "c2", signoff: { decision: "accept", by: "FAKE-uid", at: T0, note: "", edited_rationale: null } });
    const [row] = auditRows([auditEvent(1, { actor: "officer:FAKE-uid", event: "signoff_accept", clause_id: "c2" })], [f], []);
    expect(row?.edit).toBeNull();
  });
});
