/* #/ideas — the board's rules, with no DOM and no network in them.
 *
 * What order a lane is in, what a tag filter keeps, what "#ui !high" in the
 * quick-add box means, which card ▲ swaps with, where Reopen puts an idea
 * whose lane has gone. Plain functions over plain rows as PostgREST returns
 * them from ideas / idea_lanes (0075), so each is an assertion in
 * tests/js/test_ideas.mjs rather than a tap on a phone.
 *
 * It imports nothing, on purpose — results_grid.js's bargain. The moment this
 * file needs `document` or `supabase`, its tests stop meaning anything.
 */

// 1 sorts first. The labels are the ones the card editor's chips print.
export const PRIORITIES = [
  { value: 1, label: "High" },
  { value: 2, label: "Normal" },
  { value: 3, label: "Low" },
];

// Blank is "not sized yet" and is not a chip: it is what tapping the lit
// chip again goes back to.
export const SIZES = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "big", label: "Big" },
];

export const SORTS = [
  { key: "manual", label: "My order" },
  { key: "priority", label: "Priority" },
  { key: "quick", label: "Quick wins first" },
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
];

// Only the STARTING vocabulary, trendingLabels()' arrangement: allTags()
// adds every tag already in use, so the chips become whatever the board
// actually files things under.
export const STARTER_TAGS = ["UI", "Portal", "Data", "Marketing", "Strategy", "Infra"];

export const TAG_MAX = 24;
export const TAGS_PER_IDEA = 12;
export const TITLE_MAX = 200;
export const NOTES_MAX = 4000;
export const LANE_NAME_MAX = 30;

/** One tag as 0075's CHECK will accept it: no leading '#', whitespace
 *  collapsed, at most TAG_MAX characters. "" means nothing usable. */
export function cleanTag(raw) {
  return String(raw ?? "").trim().replace(/^#+/, "").replace(/\s+/g, " ").trim()
    .slice(0, TAG_MAX).trim();
}

/** Tags with duplicates removed case-insensitively — the first spelling
 *  wins, so adding "ui" to a card tagged "UI" changes nothing. */
export function uniqueTags(tags) {
  const seen = new Set();
  const out = [];
  for (const raw of tags || []) {
    const tag = cleanTag(raw);
    const key = tag.toLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
    if (out.length === TAGS_PER_IDEA) break;
  }
  return out;
}

/** "UI, portal ,  #data" → ["UI", "portal", "data"]. The editor's tag box. */
export function tagsFromText(text) {
  return uniqueTags(String(text ?? "").split(","));
}

/** The quick-add box: "Bigger tap targets #ui #portal !high".
 *
 *  `#word` is a tag and `!high` / `!low` a priority, wherever they sit in the
 *  line; everything else is the title. Only those two shortcuts, because a
 *  syntax nobody remembers is worse than one more tap in the editor. A '#'
 *  inside a word ("C#") stays in the title: a tag has to start a word. */
export function parseQuickAdd(line) {
  const tags = [];
  let priority = 2;
  const words = String(line ?? "").split(/\s+/).filter(Boolean);
  const kept = [];
  for (const word of words) {
    const lower = word.toLowerCase();
    if (lower === "!high" || lower === "!h") { priority = 1; continue; }
    if (lower === "!low" || lower === "!l") { priority = 3; continue; }
    if (/^#[^#\s]/.test(word)) { tags.push(word); continue; }
    kept.push(word);
  }
  return {
    title: kept.join(" ").slice(0, TITLE_MAX).trim(),
    tags: uniqueTags(tags),
    priority,
  };
}

export const isOpen = (idea) => !idea.closed_at;

/** Lanes in board order, ties on lane_id so two equal orders still draw the
 *  same way on every phone. */
export function sortLanes(lanes) {
  return [...(lanes || [])].sort((a, b) =>
    (a.sort_order - b.sort_order) || (a.lane_id - b.lane_id));
}

const manual = (a, b) => (a.sort_order - b.sort_order) || (a.idea_id - b.idea_id);
const created = (a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))
  || (a.idea_id - b.idea_id);
const SIZE_RANK = { small: 1, medium: 2, big: 3, "": 4 };

/** A new array in the order `mode` asks for. Every mode ends on the manual
 *  order, so "Priority" is your order within each priority, not a shuffle. */
export function sortIdeas(ideas, mode = "manual") {
  const list = [...(ideas || [])];
  if (mode === "priority") {
    return list.sort((a, b) => (a.priority - b.priority) || manual(a, b));
  }
  if (mode === "quick") {
    // Small first, unsized last — the point is "what can I finish today".
    return list.sort((a, b) =>
      ((SIZE_RANK[a.size || ""] ?? 4) - (SIZE_RANK[b.size || ""] ?? 4))
      || (a.priority - b.priority) || manual(a, b));
  }
  if (mode === "newest") return list.sort((a, b) => created(b, a));
  if (mode === "oldest") return list.sort(created);
  return list.sort(manual);
}

/** Keep ideas carrying EVERY ticked tag and EVERY word of the search text,
 *  matched case-insensitively against the title, notes and tags. */
export function filterIdeas(ideas, { tags = [], text = "" } = {}) {
  const wanted = tags.map((t) => t.toLowerCase());
  const words = String(text).toLowerCase().split(/\s+/).filter(Boolean);
  return (ideas || []).filter((idea) => {
    const own = (idea.tags || []).map((t) => t.toLowerCase());
    if (!wanted.every((t) => own.includes(t))) return false;
    if (!words.length) return true;
    const hay = [idea.title, idea.notes, ...(idea.tags || [])].join(" ").toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** Open ideas per lane: { [lane_id]: n }, every lane present, zeros too. */
export function laneCounts(ideas, lanes) {
  const counts = {};
  for (const lane of lanes || []) counts[lane.lane_id] = 0;
  for (const idea of ideas || []) {
    if (isOpen(idea) && idea.lane_id in counts) counts[idea.lane_id] += 1;
  }
  return counts;
}

/** The archive: closed ideas, most recently closed first, optionally only
 *  one outcome ('done' | 'dropped'). */
export function archivedIdeas(ideas, outcome = "") {
  return (ideas || [])
    .filter((i) => !isOpen(i) && (!outcome || i.outcome === outcome))
    .sort((a, b) => String(b.closed_at).localeCompare(String(a.closed_at))
      || (b.idea_id - a.idea_id));
}

/** The tag chips: every tag in use, most used first (then A–Z), then the
 *  starters nobody has used yet. Counted over closed ideas too — a tag from
 *  something finished last month is exactly the one worth offering again. */
export function allTags(ideas, starters = STARTER_TAGS) {
  const counts = new Map();
  const spelling = new Map();
  for (const idea of ideas || []) {
    for (const tag of idea.tags || []) {
      const key = tag.toLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
      if (!spelling.has(key)) spelling.set(key, tag);
    }
  }
  const used = [...counts.keys()].sort((a, b) =>
    (counts.get(b) - counts.get(a)) || a.localeCompare(b));
  const out = used.map((k) => spelling.get(k));
  for (const tag of starters) if (!counts.has(tag.toLowerCase())) out.push(tag);
  return out;
}

/** What ▲ ("up") or ▼ ("down") should write, for a row in `list` — a lane's
 *  ideas, or the lanes themselves, already in manual order. `key` names the
 *  id column. Returns the updates as [{ id, sort_order }], or [] at an edge.
 *
 *  Two different orders swap. Two EQUAL ones (a half-finished earlier swap,
 *  or rows created in the same instant) cannot be swapped into a change, so
 *  only the mover moves, half a step past its neighbour. */
export function moveUpdates(list, id, dir, key = "idea_id") {
  const index = list.findIndex((row) => row[key] === id);
  const other = list[index + (dir === "up" ? -1 : 1)];
  if (index < 0 || !other) return [];
  const mover = list[index];
  if (mover.sort_order !== other.sort_order) {
    return [
      { id: mover[key], sort_order: other.sort_order },
      { id: other[key], sort_order: mover.sort_order },
    ];
  }
  return [{ id: mover[key], sort_order: other.sort_order + (dir === "up" ? -0.5 : 0.5) }];
}

/** The sort_order that puts a row first (or last) in `list`. */
export function edgeOrder(list, end = "top") {
  if (!list?.length) return 0;
  const orders = list.map((row) => row.sort_order);
  return end === "top" ? Math.min(...orders) - 1 : Math.max(...orders) + 1;
}

/** Where Reopen puts an idea: back in its own lane if that still exists,
 *  else the first lane. null only when there are no lanes at all. */
export function reopenLane(idea, lanes) {
  const sorted = sortLanes(lanes);
  if (sorted.some((l) => l.lane_id === idea.lane_id)) return idea.lane_id;
  return sorted[0]?.lane_id ?? null;
}

/** The lane the URL asks for: "archive", a lane that exists, or the first
 *  lane — a stale ?lane= from a deleted lane must not draw an empty board. */
export function pickLane(param, lanes) {
  if (param === "archive") return "archive";
  const sorted = sortLanes(lanes);
  const wanted = sorted.find((l) => String(l.lane_id) === String(param ?? ""));
  return wanted ? wanted.lane_id : (sorted[0]?.lane_id ?? "archive");
}

/** "today", "3d", "2w", "4mo" — how long an idea has been waiting. */
export function ageLabel(iso, now = new Date()) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const days = Math.floor((now.getTime() - then.getTime()) / 86400000);
  if (days < 1) return "today";
  if (days < 14) return `${days}d`;
  if (days < 60) return `${Math.floor(days / 7)}w`;
  return `${Math.floor(days / 30)}mo`;
}
