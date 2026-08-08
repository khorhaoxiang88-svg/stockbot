import { getActiveExperiment } from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * The official-launch banner, required on every page: the start date and
 * strategy version of the official forward experiment (migration 022), or a
 * clear "not yet launched" state before pipeline/launch/open_experiment.py
 * has run. A separate async server component, not inlined in layout.tsx, for
 * the same reason phase-banner.tsx is: layout.tsx must stay renderable
 * outside `next build`/`next dev` for its own unit tests.
 */
export async function ExperimentBanner() {
  const { row: experiment } = getActiveExperiment();

  if (!experiment) {
    return (
      <div className="border-b border-border bg-muted/60 px-4 py-2 text-center text-sm text-muted-foreground">
        No official experiment has launched yet · all results are pre-launch.
      </div>
    );
  }

  return (
    <div className="border-b border-emerald-400/40 bg-emerald-950/80 px-4 py-2 text-center text-sm font-medium text-emerald-100">
      LIVE · {experiment.experiment_id} · strategy v{experiment.strategy_version} ·
      selection rule v{experiment.selection_rule_version} · {formatEastern(experiment.started_at)}
    </div>
  );
}
