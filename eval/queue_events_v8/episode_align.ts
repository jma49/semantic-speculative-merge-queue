/**
 * TypeScript mirror of episode pairing for deterministic cross-check (no network).
 */

export type TimelineNode = {
  __typename: string;
  createdAt: string;
  reason?: string;
};

export type Episode = {
  added_at: string | null;
  removed_at: string | null;
  reason: string | null;
  outcome_class: string;
  in_observation_week: boolean;
};

function parseTs(value: string): number {
  return Date.parse(value);
}

function inWeek(ts: string, start: string, end: string): boolean {
  const t = parseTs(ts);
  return t >= parseTs(start) && t <= parseTs(end);
}

function intervalOverlaps(
  added: string | null,
  removed: string | null,
  weekStart: string,
  weekEnd: string,
): boolean {
  if (removed && !added) return inWeek(removed, weekStart, weekEnd);
  if (!added) return false;
  const start = parseTs(added);
  const end = removed ? parseTs(removed) : Number.POSITIVE_INFINITY;
  return start <= parseTs(weekEnd) && end >= parseTs(weekStart);
}

export function buildEpisodes(nodes: TimelineNode[], weekStart: string, weekEnd: string): Episode[] {
  const lifecycle = nodes
    .filter((n) => n.__typename && n.createdAt)
    .map((n) => ({
      type: n.__typename,
      created_at: n.createdAt,
      reason: n.reason,
    }))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));

  const episodes: Episode[] = [];
  let openAdded: (typeof lifecycle)[0] | null = null;

  for (const ev of lifecycle) {
    if (ev.type === "AddedToMergeQueueEvent") {
      if (openAdded) {
        episodes.push({
          added_at: openAdded.created_at,
          removed_at: null,
          reason: null,
          outcome_class: "truncated_prior_enqueue_superseded",
          in_observation_week: intervalOverlaps(openAdded.created_at, null, weekStart, weekEnd),
        });
      }
      openAdded = ev;
    } else if (ev.type === "RemovedFromMergeQueueEvent") {
      if (!openAdded) {
        episodes.push({
          added_at: null,
          removed_at: ev.created_at,
          reason: ev.reason ?? null,
          outcome_class: "right_censored_removed_without_added",
          in_observation_week: intervalOverlaps(null, ev.created_at, weekStart, weekEnd),
        });
        continue;
      }
      const reason = ev.reason ?? null;
      let outcome = reason === "merged" ? "merged" : "removed";
      if (reason === "failed_checks") outcome = "failed_checks";
      episodes.push({
        added_at: openAdded.created_at,
        removed_at: ev.created_at,
        reason,
        outcome_class: outcome,
        in_observation_week: intervalOverlaps(openAdded.created_at, ev.created_at, weekStart, weekEnd),
      });
      openAdded = null;
    }
  }
  if (openAdded) {
    episodes.push({
      added_at: openAdded.created_at,
      removed_at: null,
      reason: null,
      outcome_class: "left_censored_episode_open",
      in_observation_week: intervalOverlaps(openAdded.created_at, null, weekStart, weekEnd),
    });
  }
  return episodes;
}