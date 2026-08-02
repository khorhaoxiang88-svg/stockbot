-- 014 up: DB-level backstop for "at most one open position per (security, horizon)".
--
-- F11 (migration 013) dropped F10's `positions` table and its partial unique
-- index in favour of `paper_positions`, on the reasoning that the rule now
-- lives at selection time: pipeline/selection/rules.py suppresses a candidate
-- with reason 'open_position' before it can ever reach execution. That is
-- true, but it is a claim about the CODE PATH, not a guarantee the database
-- holds independent of it -- and every other table in this schema (append-only
-- xbrl_facts, the formula CHECKs on scores and dilution_signals) enforces its
-- invariant at the storage layer precisely so a future bug in application code
-- cannot silently violate it. This migration brings paper_positions up to that
-- same standard.
--
-- WHY A TRIGGER, NOT A PARTIAL UNIQUE INDEX. paper_positions has no
-- security_id column -- only candidate_id, resolved through
-- research_candidates -- and a SQLite partial index predicate cannot express a
-- join. Denormalizing security_id onto paper_positions just to index it would
-- add a column whose only job is duplicating data another table already owns.
-- A BEFORE trigger has no such restriction: it can run the join as a subquery
-- at the moment a row is written, which is exactly what is needed here and
-- costs nothing structurally.
--
-- TWO TRIGGERS, NOT ONE. A row can only ever ENTER the 'open' status two ways:
-- being inserted that way, or being updated into it from something else. Only
-- the INSERT path exists in the current execution engine -- nothing in
-- pipeline/execution/compute.py ever moves a row from 'pending_resolution' or
-- 'closed' back to 'open' -- but "nothing does this today" describes the code,
-- not a guarantee. The UPDATE trigger costs nothing and turns that description
-- into an invariant no future code change can violate, matching the standard
-- the rest of this schema already holds itself to.

CREATE TRIGGER IF NOT EXISTS paper_positions_one_open_per_security_horizon_insert
BEFORE INSERT ON paper_positions
WHEN NEW.status = 'open'
BEGIN
    SELECT RAISE(ABORT, 'another position is already open for this security at this horizon')
     WHERE EXISTS (
        SELECT 1
          FROM paper_positions p
          JOIN research_candidates existing ON existing.candidate_id = p.candidate_id
          JOIN research_candidates incoming ON incoming.candidate_id = NEW.candidate_id
         WHERE p.status = 'open'
           AND p.horizon_days = NEW.horizon_days
           AND existing.security_id = incoming.security_id
    );
END;

CREATE TRIGGER IF NOT EXISTS paper_positions_one_open_per_security_horizon_update
BEFORE UPDATE ON paper_positions
WHEN NEW.status = 'open' AND OLD.status <> 'open'
BEGIN
    SELECT RAISE(ABORT, 'another position is already open for this security at this horizon')
     WHERE EXISTS (
        SELECT 1
          FROM paper_positions p
          JOIN research_candidates existing ON existing.candidate_id = p.candidate_id
          JOIN research_candidates incoming ON incoming.candidate_id = NEW.candidate_id
         WHERE p.status = 'open'
           AND p.horizon_days = NEW.horizon_days
           AND existing.security_id = incoming.security_id
           AND p.position_id <> NEW.position_id
    );
END;
