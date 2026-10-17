// Reads the FAKE demo data written by scripts/seed_demo.py from the local emulator through the app's own
// converters and selectors, so the backend's stored JSON and this app's types are checked end to end.
// Run: npm run seed-demo && npm run test:emulator
import { initializeApp } from "firebase/app";
import { connectAuthEmulator, getAuth, signInAnonymously } from "firebase/auth";
import { collection, connectFirestoreEmulator, doc, getDoc, getDocs, getFirestore, onSnapshot, orderBy, query } from "firebase/firestore";
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import { findingConverter, parseRun, runConverter, stepConverter } from "./convert";
import { liveRunQuery } from "./hooks";
import { badgeFor, clauseRows, injectionFor, outdatedChain, summarize } from "./selectors";

const host = process.env.FIRESTORE_EMULATOR_HOST ?? "localhost:8081";
const authUrl = process.env.FIREBASE_AUTH_EMULATOR_URL ?? "http://localhost:9099";
const app = initializeApp({ apiKey: "fake-api-key-for-emulator", projectId: "argus-local", appId: "fake", authDomain: "fake" }, "emulator-test");
const db = getFirestore(app);
const [h, p] = host.split(":");
connectFirestoreEmulator(db, h ?? "localhost", Number(p));
const auth = getAuth(app);
connectAuthEmulator(auth, authUrl, { disableWarnings: true });

async function seededSample() {
  // The emulator is shared with the backend's rule tests, whose fixtures are not runs: pick the seeded one first.
  const raw = (await getDocs(collection(db, "samples"))).docs.find((d) => String(d.get("policy_title")).includes("FAKE FinFast"));
  const sample = raw ? parseRun(raw.data(), raw.ref.path) : undefined;
  if (!sample?.saved || sample.status !== "done") throw new Error("No seeded FinFast sample in the emulator: run `npm run seed-demo` first");
  const base = collection(db, "samples", sample.run_id, "findings").withConverter(findingConverter);
  const findings = (await getDocs(base)).docs.map((d) => d.data());
  const steps = (
    await getDocs(query(collection(db, "samples", sample.run_id, "steps").withConverter(stepConverter), orderBy("seq")))
  ).docs.map((d) => d.data());
  return { sample, findings, steps };
}

describe("seeded demo data (emulator)", () => {
  it("covers the five hero moments through the app's selectors", async () => {
    const { sample, findings, steps } = await seededSample();
    // FAKE data is marked seeded (save_sample refuses it) and carries both library dates, as a real run does.
    expect(sample.seeded).toBe(true);
    expect(sample.rbi_as_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const rows = clauseRows(sample, findings);
    expect(rows.every((r) => r.state === "done")).toBe(true);
    expect(rows).toHaveLength(sample.clauses.length);

    const counts = summarize(sample, findings, steps);
    expect(counts.byLabel.outdated_reference).toBe(sample.summary.outdated_reference);
    expect(counts.byLabel.gap).toBe(sample.summary.gap);
    expect(counts.byLabel.conflict).toBe(sample.summary.conflict);
    expect(counts.monitorStopped).toBe(sample.summary.monitor_stops);
    expect(counts.monitorRevised).toBe(sample.summary.monitor_revisions);

    const chain = findings.map(outdatedChain).find((c) => c !== null);
    expect(chain?.withdrawal?.by).toBeTruthy();
    expect(chain?.withdrawal?.on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(chain?.current?.in_force).toBe(true);

    const badges = findings.map((f) => badgeFor(f, steps));
    expect(badges.some((b) => b.state === "revised" && b.failedChecks.some((c) => c.name === "paragraph_exists"))).toBe(true);
    const stopped = findings.filter((f) => badgeFor(f, steps).state === "stopped");
    expect(stopped).toHaveLength(1);
    expect(injectionFor(stopped[0]?.clause_id ?? "", steps)?.quote).toMatch(/pre-cleared by RBI/);
    expect(badges.some((b) => b.state === "needs_review")).toBe(true);
    expect(badges.some((b) => b.state === "verified")).toBe(true);
  });

  it("has no eval_results/latest, so the Evaluation page points to the official evaluation in the repository", async () => {
    expect((await getDoc(doc(db, "eval_results", "latest"))).exists()).toBe(false);
  });

  it("live runs seeded for this user: one running with clauses in progress and waiting, one with a failed clause", async () => {
    const { user } = await signInAnonymously(auth);
    const out = execFileSync("../backend/.venv/bin/python", ["scripts/seed_demo.py", "--owner-uid", user.uid], {
      encoding: "utf8",
      env: { ...process.env, FIRESTORE_EMULATOR_HOST: host },
    });
    const ids = [...out.matchAll(/runs\/([0-9a-f]{32})/g)].map((m) => m[1] ?? "");
    expect(ids).toHaveLength(2);
    const runs = await Promise.all(
      ids.map(async (id) => {
        const run = (await getDoc(doc(db, "runs", id).withConverter(runConverter))).data();
        if (!run) throw new Error(`runs/${id} missing`);
        const findings = (await getDocs(collection(db, "runs", id, "findings").withConverter(findingConverter))).docs.map((d) => d.data());
        const steps = (await getDocs(query(collection(db, "runs", id, "steps").withConverter(stepConverter), orderBy("seq")))).docs.map((d) =>
          d.data(),
        );
        return { run, rows: clauseRows(run, findings, { findingsLoaded: true, steps }), findings };
      }),
    );
    expect(runs.every((r) => r.run.seeded)).toBe(true);
    const running = runs.find((r) => r.run.status === "running");
    // The clause the trace shows in progress spins; the ones with no step yet say they are waiting.
    expect(running?.rows.filter((r) => r.state === "reviewing")).toHaveLength(1);
    expect(running?.rows.some((r) => r.state === "queued")).toBe(true);
    const failed = runs.find((r) => r.run.status === "done");
    expect(failed?.rows.filter((r) => r.state === "failed")).toHaveLength(1);
    expect(failed?.findings.some((f) => f.signoff?.decision === "edit" && f.signoff.edited_rationale)).toBe(true);
  });

  // UI_SPEC 16.4 under the real firestore.rules: the run listener (liveRunQuery) is allowed before the API creates
  // the run, sees it appear, and the subcollections become readable to the owner once it exists. Another user sees
  // nothing and is refused a direct read. The run is written with the emulator's admin token, as the API would.
  it("lets the owner watch a live run before the API creates it, and nobody else", async () => {
    const other = await signInAnonymously(getAuth(otherApp));
    const { user } = await signInAnonymously(auth);
    const runId = crypto.randomUUID().replaceAll("-", "");
    const sizes: number[] = [];
    let stop = (): void => undefined;
    const appeared = new Promise<void>((resolve, reject) => {
      stop = onSnapshot(
        liveRunQuery(db, user.uid, runId),
        (snap) => {
          sizes.push(snap.size);
          if (snap.size > 0) resolve();
        },
        reject,
      );
    });
    try {
      await adminWrite(`runs?documentId=${runId}`, { owner_uid: { stringValue: user.uid }, run_id: { stringValue: runId } });
      await adminWrite(`runs/${runId}/steps?documentId=0001`, { seq: { integerValue: "1" } });
      await appeared;
      expect(sizes[0]).toBe(0); // attached (and allowed) before the run existed
      expect((await getDocs(collection(db, "runs", runId, "steps"))).size).toBe(1);

      const otherDb = getFirestore(otherApp);
      expect((await getDocs(liveRunQuery(otherDb, other.user.uid, runId))).size).toBe(0);
      await expect(getDoc(doc(otherDb, "runs", runId))).rejects.toMatchObject({ code: "permission-denied" });
      await expect(getDocs(collection(otherDb, "runs", runId, "steps"))).rejects.toMatchObject({ code: "permission-denied" });
    } finally {
      stop();
      await adminDelete(`runs/${runId}/steps/0001`);
      await adminDelete(`runs/${runId}`);
    }
  });
});

const otherApp = initializeApp({ apiKey: "fake-api-key-for-emulator", projectId: "argus-local", appId: "fake", authDomain: "fake" }, "emulator-test-other");
connectFirestoreEmulator(getFirestore(otherApp), h ?? "localhost", Number(p));
connectAuthEmulator(getAuth(otherApp), authUrl, { disableWarnings: true });

const restBase = `http://${host}/v1/projects/argus-local/databases/(default)/documents`;

async function adminWrite(path: string, fields: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${restBase}/${path}`, {
    method: "POST",
    headers: { Authorization: "Bearer owner", "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  if (!res.ok) throw new Error(`Admin write ${path} failed: ${res.status} ${await res.text()}`);
}

async function adminDelete(path: string): Promise<void> {
  const res = await fetch(`${restBase}/${path}`, { method: "DELETE", headers: { Authorization: "Bearer owner" } });
  if (!res.ok) throw new Error(`Admin delete ${path} failed: ${res.status} ${await res.text()}`);
}
