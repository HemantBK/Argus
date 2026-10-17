// The Review screen wired to Firestore listeners and the API. The app shell renders it for #/run/<id> and
// #/sample/<id>; run-level banners (start problems, live review unavailable, run failed, with the saved-run
// offer) are the shell's (shell/banners.tsx), so this screen shows only what is inside the run.
import { useCallback, type ReactNode } from "react";
import { signoff, type SignoffBody } from "../data/api";
import { useRunData } from "../data/hooks";
import type { RunSource } from "../data/types";
import { SAMPLE_POLICIES } from "../start/samplePolicies";
import { ReviewWorkspace } from "./ReviewWorkspace";

export function ReviewScreen({ runId, source }: { runId: string; source: RunSource }): ReactNode {
  const data = useRunData(runId, source);
  const onSignoff = useCallback(
    async (clauseId: string, body: SignoffBody) => {
      await signoff(runId, clauseId, body);
    },
    [runId],
  );
  const policyId = data.run.status === "ready" ? (data.run.data?.policy_id ?? null) : null;
  // A pasted clause (source custom) is the officer's own text, never a sample: only a sample file can say synthetic.
  const custom = data.run.status === "ready" && data.run.data?.source === "custom";
  const synthetic = !custom && SAMPLE_POLICIES.policies.some((p) => p.policyId === policyId && p.synthetic);
  return (
    <ReviewWorkspace
      source={source}
      run={data.run}
      steps={data.steps}
      findings={data.findings}
      audit={data.audit}
      synthetic={synthetic}
      onSignoff={onSignoff}
    />
  );
}
