import { getActiveExperiment } from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * The official-launch banner, required on every page: the start date and
 * strategy version of the official forward experiment (migration 022), or a
 * clear "not yet launched" state before pipeline/launch/open_experiment.py
 * has run. A separate async server component, not inlined in layout.tsx,
 * because layout.tsx must stay renderable outside `next build`/`next dev`
 * for its own unit tests (see site-footer.tsx for the same reasoning).
 */
export async function ExperimentBanner() {
  const { row: experiment } = getActiveExperiment();

  if (!experiment) {
    return (
      <div className="border-b border-border bg-muted/60 px-4 py-2 text-center text-sm text-muted-foreground">
        No official experiment has launched yet. Every result on this site is
        pre-launch (Phase F fixture / Phase S paper trades) and will be
        permanently excluded from official statistics once one does.
      </div>
    );
  }

  return (
    <div className="border-b border-emerald-400/40 bg-emerald-950/80 px-4 py-2 text-center text-sm font-medium text-emerald-100">
      Official experiment {experiment.experiment_id} live since{" "}
      {formatEastern(experiment.started_at)} — strategy v{experiment.strategy_version},
      selection rule v{experiment.selection_rule_version}. Everything before this
      date is permanently pre-launch, excluded from official statistics.
    </div>
  );
}
