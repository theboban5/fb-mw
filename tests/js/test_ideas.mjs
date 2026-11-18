/* #/ideas' rules, tested without a browser.
 *
 *     node --test tests/js/            (or: npm test)
 *
 * The questions are the ones a board gets quietly wrong: whether "Priority"
 * keeps your order inside each priority, whether ▲ on two cards with the same
 * order does anything at all, what "#ui !high" means, and where Reopen puts
 * an idea whose lane was deleted. ideas.js imports nothing, which is what
 * keeps these honest.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  cleanTag, uniqueTags, tagsFromText, parseQuickAdd, sortLanes, sortIdeas,
  filterIdeas, laneCounts, archivedIdeas, allTags, moveUpdates, edgeOrder,
  reopenLane, pickLane, ageLabel,
} from "../../static/report/ideas.js";

// A row as PostgREST returns it from public.ideas (0075).
const idea = (idea_id, over = {}) => ({
  idea_id, title: `Idea ${idea_id}`, notes: "", lane_id: 1, tags: [],
  priority: 2, size: "", sort_order: idea_id, closed_at: null, outcome: null,
  created_at: `2026-11-${String(idea_id).padStart(2, "0")}T10:00:00Z`,
  ...over,
});
const ids = (list) => list.map((i) => i.idea_id);

const LANES = [
  { lane_id: 3, name: "Doing", sort_order: 3 },
  { lane_id: 1, name: "Inbox", sort_order: 1 },
  { lane_id: 2, name: "Next up", sort_order: 2 },
];

test("cleanTag drops the hash, collapses space and caps the length", () => {
  assert.equal(cleanTag("  #Marketing  "), "Marketing");
  assert.equal(cleanTag("##two  words"), "two words");
  assert.equal(cleanTag("x".repeat(40)).length, 24);
  assert.equal(cleanTag("#"), "");
});

test("uniqueTags keeps the first spelling and at most twelve", () => {
  assert.deepEqual(uniqueTags(["UI", "ui", " Ui ", "Data"]), ["UI", "Data"]);
  const many = Array.from({ length: 20 }, (_, i) => `t${i}`);
  assert.equal(uniqueTags(many).length, 12);
  assert.deepEqual(tagsFromText("UI, portal ,, #data"), ["UI", "portal", "data"]);
});

test("parseQuickAdd reads #tags and !high/!low anywhere in the line", () => {
  assert.deepEqual(parseQuickAdd("Bigger tap targets #ui #Portal !high"),
    { title: "Bigger tap targets", tags: ["ui", "Portal"], priority: 1 });
  assert.deepEqual(parseQuickAdd("!low #infra Move to a private repo (lol)"),
    { title: "Move to a private repo (lol)", tags: ["infra"], priority: 3 });
  // A plain line is a title, normal priority, no tags.
  assert.deepEqual(parseQuickAdd("  WhatsApp channel launch "),
    { title: "WhatsApp channel launch", tags: [], priority: 2 });
});

test("parseQuickAdd leaves a # that does not start a word in the title", () => {
  assert.equal(parseQuickAdd("Learn C# properly").title, "Learn C# properly");
  assert.equal(parseQuickAdd("Score # on cards").title, "Score # on cards");
  // Nothing but shortcuts is no title — the screen refuses it.
  assert.equal(parseQuickAdd("#ui !high").title, "");
});

test("lanes sort by order, ties on id", () => {
  assert.deepEqual(sortLanes(LANES).map((l) => l.lane_id), [1, 2, 3]);
  const tied = [{ lane_id: 9, sort_order: 1 }, { lane_id: 4, sort_order: 1 }];
  assert.deepEqual(sortLanes(tied).map((l) => l.lane_id), [4, 9]);
});

test("My order is sort_order, ties on idea_id", () => {
  const list = [idea(3, { sort_order: 1 }), idea(1, { sort_order: 2 }),
                idea(2, { sort_order: 1 })];
  assert.deepEqual(ids(sortIdeas(list, "manual")), [2, 3, 1]);
});

test("Priority keeps my order inside each priority", () => {
  const list = [idea(1, { priority: 2 }), idea(2, { priority: 1 }),
                idea(3, { priority: 2 }), idea(4, { priority: 1 })];
  assert.deepEqual(ids(sortIdeas(list, "priority")), [2, 4, 1, 3]);
});

test("Quick wins puts small first and unsized last", () => {
  const list = [idea(1, { size: "" }), idea(2, { size: "big" }),
                idea(3, { size: "small" }), idea(4, { size: "medium" }),
                idea(5, { size: "small", priority: 1 })];
  assert.deepEqual(ids(sortIdeas(list, "quick")), [5, 3, 4, 2, 1]);
});

test("Newest and Oldest read created_at", () => {
  const list = [idea(2), idea(5), idea(1)];
  assert.deepEqual(ids(sortIdeas(list, "newest")), [5, 2, 1]);
  assert.deepEqual(ids(sortIdeas(list, "oldest")), [1, 2, 5]);
});

test("sortIdeas never reorders the array it was given", () => {
  const list = [idea(2), idea(1)];
  sortIdeas(list, "manual");
  assert.deepEqual(ids(list), [2, 1]);
});

test("filterIdeas needs every tag and every word, any case", () => {
  const list = [
    idea(1, { tags: ["UI", "Portal"], title: "Bigger steppers" }),
    idea(2, { tags: ["ui"], notes: "the grid's steppers are 28px" }),
    idea(3, { tags: ["Marketing"], title: "WhatsApp channel" }),
  ];
  assert.deepEqual(ids(filterIdeas(list, { tags: ["ui"] })), [1, 2]);
  assert.deepEqual(ids(filterIdeas(list, { tags: ["UI", "portal"] })), [1]);
  assert.deepEqual(ids(filterIdeas(list, { text: "steppers" })), [1, 2]);
  assert.deepEqual(ids(filterIdeas(list, { text: "STEPPERS 28px" })), [2]);
  // A tag name is searchable as text too.
  assert.deepEqual(ids(filterIdeas(list, { text: "marketing" })), [3]);
  assert.deepEqual(ids(filterIdeas(list, {})), [1, 2, 3]);
});

test("laneCounts counts open ideas only, and every lane", () => {
  const list = [idea(1), idea(2, { lane_id: 2 }),
                idea(3, { closed_at: "2026-11-10T00:00:00Z", outcome: "done" })];
  assert.deepEqual(laneCounts(list, LANES), { 1: 1, 2: 1, 3: 0 });
});

test("the archive is closed ideas, most recently closed first", () => {
  const list = [
    idea(1, { closed_at: "2026-11-01T00:00:00Z", outcome: "done" }),
    idea(2),
    idea(3, { closed_at: "2026-11-09T00:00:00Z", outcome: "dropped" }),
    idea(4, { closed_at: "2026-11-05T00:00:00Z", outcome: "done" }),
  ];
  assert.deepEqual(ids(archivedIdeas(list)), [3, 4, 1]);
  assert.deepEqual(ids(archivedIdeas(list, "done")), [4, 1]);
  assert.deepEqual(ids(archivedIdeas(list, "dropped")), [3]);
});

test("allTags: most used first, then the unused starters", () => {
  const list = [idea(1, { tags: ["Data", "UI"] }), idea(2, { tags: ["ui"] }),
                idea(3, { tags: ["Derby"], closed_at: "x", outcome: "done" })];
  assert.deepEqual(allTags(list, ["UI", "Marketing"]),
    ["UI", "Data", "Derby", "Marketing"]);
});

test("moveUpdates swaps two different orders", () => {
  const list = [idea(1, { sort_order: 1 }), idea(2, { sort_order: 5 }),
                idea(3, { sort_order: 9 })];
  assert.deepEqual(moveUpdates(list, 2, "up"),
    [{ id: 2, sort_order: 1 }, { id: 1, sort_order: 5 }]);
  assert.deepEqual(moveUpdates(list, 2, "down"),
    [{ id: 2, sort_order: 9 }, { id: 3, sort_order: 5 }]);
});

test("moveUpdates on two equal orders still moves the card", () => {
  // Swapping 4 for 4 would write twice and change nothing on screen.
  const list = [idea(1, { sort_order: 4 }), idea(2, { sort_order: 4 })];
  assert.deepEqual(moveUpdates(list, 2, "up"), [{ id: 2, sort_order: 3.5 }]);
  assert.deepEqual(moveUpdates(list, 1, "down"), [{ id: 1, sort_order: 4.5 }]);
  // And the result really is the other way round.
  const moved = list.map((i) => (i.idea_id === 2 ? { ...i, sort_order: 3.5 } : i));
  assert.deepEqual(ids(sortIdeas(moved)), [2, 1]);
});

test("moveUpdates does nothing at an edge, and works on lanes", () => {
  const list = [idea(1), idea(2)];
  assert.deepEqual(moveUpdates(list, 1, "up"), []);
  assert.deepEqual(moveUpdates(list, 2, "down"), []);
  assert.deepEqual(moveUpdates(list, 99, "up"), []);
  assert.deepEqual(moveUpdates(sortLanes(LANES), 3, "up", "lane_id"),
    [{ id: 3, sort_order: 2 }, { id: 2, sort_order: 3 }]);
});

test("edgeOrder puts a new idea above or below everything", () => {
  const list = [idea(1, { sort_order: -2 }), idea(2, { sort_order: 7 })];
  assert.equal(edgeOrder(list, "top"), -3);
  assert.equal(edgeOrder(list, "bottom"), 8);
  assert.equal(edgeOrder([], "top"), 0);
});

test("Reopen goes back to its own lane, else the first", () => {
  assert.equal(reopenLane(idea(1, { lane_id: 3 }), LANES), 3);
  assert.equal(reopenLane(idea(1, { lane_id: null }), LANES), 1);
  assert.equal(reopenLane(idea(1, { lane_id: 42 }), LANES), 1);
  assert.equal(reopenLane(idea(1), []), null);
});

test("pickLane never draws a lane that is not there", () => {
  assert.equal(pickLane("2", LANES), 2);
  assert.equal(pickLane("archive", LANES), "archive");
  assert.equal(pickLane("42", LANES), 1);
  assert.equal(pickLane(null, LANES), 1);
  assert.equal(pickLane(null, []), "archive");
});

test("ageLabel", () => {
  const now = new Date("2026-11-18T12:00:00Z");
  assert.equal(ageLabel("2026-11-18T08:00:00Z", now), "today");
  assert.equal(ageLabel("2026-11-15T12:00:00Z", now), "3d");
  assert.equal(ageLabel("2026-11-01T12:00:00Z", now), "2w");
  assert.equal(ageLabel("2026-07-01T12:00:00Z", now), "4mo");
  assert.equal(ageLabel("not a date", now), "");
});
