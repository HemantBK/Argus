// The Review screen wired to Firestore listeners and the API. The app shell renders it for #/run/<id> and
// #/sample/<id>; run-level banners (start problems, live review unavailable, run failed, with the saved-run
// offer) are the shell's (shell/banners.tsx), so this screen shows only what is inside the run.
import { useCallback, type ReactNode } from "react";
import { signoff, type SignoffBody } from "../data/api";
import { useRunData } from "../data/hooks";
import type { RunSource } from "../data/types";
import { SAMPLE_POLICIES } from "../start/samplePolicies";
import { ReviewWorkspace } from "./ReviewWorkspace";
import { isSyntheticSample } from "./view";

export function ReviewScreen({ runId, source }: { runId: string; source: RunSource }): ReactNode {
  const data = useRunData(runId, source);
  const onSignoff = useCallback(
    async (clauseId: string, body: SignoffBody) => {
      await signoff(runId, clauseId, body);
    },
    [runId],
  );
  const synthetic = isSyntheticSample(data.run.status === "ready" ? data.run.data : null, SAMPLE_POLICIES.policies);
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
