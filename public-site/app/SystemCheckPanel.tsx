"use client";

import { useEffect, useState } from "react";

import {
  classify,
  fetchStatus,
  DISPLAY_COLOR_VAR,
  DISPLAY_LABEL,
  STATUS_URL,
} from "./status.mjs";

const POLL_MS = 60_000;

/**
 * Genuine live status of the local Stockbot pipeline, read from a public-
 * safe status.json committed to this repo's `bot-status` branch (free:
 * fetched straight from raw.githubusercontent.com, no backend of this
 * site's own). Defaults to "Status unavailable" -- never a fake "Healthy"
 * -- until a fetch actually succeeds. See pipeline/scheduler/
 * publish_status.py for what writes it and app/status.mjs for the
 * staleness/color logic this only renders.
 */
export function SystemCheckPanel() {
  const [status, setStatus] = useState(null);
  const [checkedAt, setCheckedAt] = useState(() => Date.now());
  const [everSucceeded, setEverSucceeded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      const result = await fetchStatus(STATUS_URL);
      if (cancelled) return;
      setCheckedAt(Date.now());
      if (result) {
        setStatus(result);
        setEverSucceeded(true);
      } else if (!everSucceeded) {
        // Never fetched successfully at all -- stays "unavailable" rather
        // than showing a stale null render.
        setStatus(null);
      }
      // A single failed poll after a prior success does NOT clear status:
      // classify()'s own staleness check (age vs last_activity_at) is what
      // downgrades a genuinely-stale reading, not a single network blip.
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const display = classify(status, checkedAt);
  const color = `var(${DISPLAY_COLOR_VAR[display]})`;
  const label = DISPLAY_LABEL[display];

  return (
    <article className="system-check" aria-live="polite">
      <span>System check</span>
      <strong style={{ color }}>
        <i className="status-dot" style={{ background: color }} />
        {label}
      </strong>
      <small>
        {status?.current_stage
          ? `Stage: ${status.current_stage}`
          : status
            ? "Waiting for next scheduled run"
            : "Not connected live"}
      </small>
      {status?.progress ? (
        <small className="status-progress">
          {status.progress.completed}/{status.progress.total} securities
        </small>
      ) : null}
    </article>
  );
}
