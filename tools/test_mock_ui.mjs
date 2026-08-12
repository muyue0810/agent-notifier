#!/usr/bin/env node
"use strict";

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";


const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTML_PATH = path.join(HERE, "mock_box.html");
const HTML = fs.readFileSync(HTML_PATH, "utf8");
const SCRIPT_MATCH = HTML.match(/<script>([\s\S]*?)<\/script>/);

assert.ok(SCRIPT_MATCH, "mock_box.html must contain an inline script");


class ClassList {
  constructor(element) {
    this.element = element;
    this.values = new Set();
  }

  reset(value) {
    this.values = new Set(String(value || "").split(/\s+/).filter(Boolean));
    this.sync();
  }

  sync() {
    this.element._className = [...this.values].join(" ");
  }

  add(...tokens) {
    tokens.forEach((token) => this.values.add(token));
    this.sync();
  }

  remove(...tokens) {
    tokens.forEach((token) => this.values.delete(token));
    this.sync();
  }

  contains(token) {
    return this.values.has(token);
  }

  toggle(token, force) {
    const enabled = force === undefined ? !this.contains(token) : Boolean(force);
    if (enabled) this.values.add(token);
    else this.values.delete(token);
    this.sync();
    return enabled;
  }
}


class Element {
  constructor(tagName, id = "") {
    this.tagName = String(tagName).toUpperCase();
    this.id = id;
    this.type = "";
    this.title = "";
    this.value = "";
    this.disabled = false;
    this.dataset = {};
    this.attributes = new Map();
    this.children = [];
    this.listeners = new Map();
    this.parentNode = null;
    this._className = "";
    this._textContent = "";
    this.classList = new ClassList(this);
  }

  get className() {
    return this._className;
  }

  set className(value) {
    this.classList.reset(value);
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    this.children = [];
  }

  append(...items) {
    for (const item of items) {
      const child = item instanceof Element ? item : new TextNode(item);
      child.parentNode = this;
      this.children.push(child);
    }
  }

  replaceChildren(...items) {
    this.children = [];
    this._textContent = "";
    this.append(...items);
  }

  setAttribute(name, value) {
    const text = String(value);
    this.attributes.set(name, text);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = text;
    }
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(callback);
    this.listeners.set(type, callbacks);
  }

  async click() {
    if (this.disabled) return;
    for (const callback of this.listeners.get("click") || []) {
      await callback({ currentTarget: this, target: this });
    }
  }
}


class TextNode extends Element {
  constructor(value) {
    super("#text");
    this._textContent = String(value);
  }
}


class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.selectors = new Map();
    this.add("connection", "span", "connection disconnected");
    this.add("connection-label", "span");
    this.add("session-grid", "main", "session-grid empty");
    this.add("mute-button", "button", "tool-button");
    this.add("mute-label", "span", "tool-label").textContent = "Mute";
    this.add("ack-button", "button", "tool-button").disabled = true;
    this.add("more-button", "button", "tool-button");
    this.add("history-layer", "section", "history-layer");
    this.add("history-items", "div", "history-items");
    this.add("history-back", "button", "history-back");
    this.add("active-sessions", "div");
    this.add("transport", "div");
    this.add("json-preview", "pre");
    this.add("error-banner", "div", "error-banner");
    this.add("demo-button", "button", "demo-button");
    this.add("session-id", "input").value = "mock:session-a";
    this.add("source", "input").value = "Codex:kernel#A";
    this.add("message", "input").value = "running tests";

    this.selectors.set("[data-session]", [
      this.dataButton({ session: "mock:session-a", source: "Codex:kernel#A" }),
      this.dataButton({ session: "mock:session-b", source: "Claude:gadget#B" }),
    ]);
    this.selectors.set("[data-state]", [
      this.dataButton({ state: "processing" }),
      this.dataButton({ state: "done" }),
      this.dataButton({ state: "wait" }),
      this.dataButton({ state: "approval" }),
      this.dataButton({ state: "offline" }),
    ]);
  }

  add(id, tagName, className = "") {
    const element = new Element(tagName, id);
    element.className = className;
    this.elements.set(id, element);
    return element;
  }

  dataButton(dataset) {
    const button = new Element("button");
    button.dataset = { ...dataset };
    return button;
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  createElement(tagName) {
    return new Element(tagName);
  }

  querySelectorAll(selector) {
    return this.selectors.get(selector) || [];
  }
}


const STATE_PRIORITY = {
  offline: 0,
  processing: 10,
  done: 20,
  wait: 30,
  approval: 40,
};


function clone(value) {
  return JSON.parse(JSON.stringify(value));
}


function priorityDisplay(sessions) {
  if (!sessions.length) {
    return {
      session_id: "",
      source: "",
      state: "offline",
      message: "",
      event_id: "",
      level: "silent",
      sequence: 0,
    };
  }
  return sessions.reduce((best, item) => {
    const score = [STATE_PRIORITY[item.state] || 0, item.sequence || 0];
    const bestScore = [STATE_PRIORITY[best.state] || 0, best.sequence || 0];
    return score[0] > bestScore[0] || (score[0] === bestScore[0] && score[1] > bestScore[1])
      ? item
      : best;
  });
}


function makeSessions(count, states = ["processing", "done", "wait", "approval"]) {
  return Array.from({ length: count }, (_, index) => {
    const state = states[index % states.length];
    const agent = index % 2 ? "Claude" : "Codex";
    return {
      session_id: `session-${index}`,
      source: `${agent}:project-${index}#${index}`,
      state,
      message: `message-${index}`,
      event_id: `event-${index}`,
      level: state === "processing" ? "silent" : state === "approval" || state === "wait" ? "attention" : "info",
      sequence: index + 1,
    };
  });
}


class FakeBackend {
  constructor(sessions = []) {
    this.sessions = clone(sessions);
    this.muted = false;
    this.history = ["recent event"];
    this.posts = [];
    this.sequence = sessions.length;
  }

  state() {
    return {
      app: "agent_notifier",
      proto: 1,
      transport: "mock-ui",
      service_online: true,
      muted: this.muted,
      display: clone(priorityDisplay(this.sessions)),
      sessions: clone(this.sessions),
      history: clone(this.history),
      active_sessions: this.sessions.length,
      updated_at: 1,
    };
  }

  setSessions(sessions) {
    this.sessions = clone(sessions);
    this.sequence = Math.max(this.sequence, sessions.length);
  }

  apply(payload) {
    this.posts.push(clone(payload));
    if (payload.action === "mute") {
      this.muted = payload.on === undefined ? !this.muted : Boolean(payload.on);
      return { ok: true, muted: this.muted };
    }
    if (payload.action === "ack") {
      const before = this.sessions.length;
      this.sessions = this.sessions.filter((item) => item.event_id !== payload.event_id);
      return { ok: true, removed: this.sessions.length !== before };
    }
    if (payload.action === "event") {
      const index = this.sessions.findIndex((item) => item.session_id === payload.session_id);
      if (payload.state === "offline") {
        if (index >= 0) this.sessions.splice(index, 1);
      } else {
        this.sequence += 1;
        const item = { ...payload, sequence: this.sequence };
        if (index >= 0) this.sessions[index] = item;
        else this.sessions.push(item);
        this.history.unshift(`${payload.source} — ${payload.state}: ${payload.message}`);
        this.history = this.history.slice(0, 5);
      }
      return { ok: true, display: clone(priorityDisplay(this.sessions)) };
    }
    return { ok: true };
  }
}


async function flushTasks() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}


async function runUI(sessions = []) {
  const document = new FakeDocument();
  const backend = new FakeBackend(sessions);
  const intervals = [];
  const window = {};
  const context = vm.createContext({
    console,
    document,
    window,
    setInterval(callback) {
      intervals.push(callback);
      return intervals.length;
    },
    clearInterval() {},
    fetch: async (requestPath, options = {}) => {
      let payload;
      if (requestPath === "/api/state") payload = backend.state();
      else if (requestPath === "/api/action") payload = backend.apply(JSON.parse(options.body));
      else return { ok: false, status: 404, json: async () => ({ ok: false, error: "not found" }) };
      return { ok: true, status: 200, json: async () => clone(payload) };
    },
  });
  window.window = window;
  vm.runInContext(SCRIPT_MATCH[1], context, { filename: HTML_PATH });
  await flushTasks();
  assert.equal(intervals.length, 1, "UI should install one polling interval");
  return {
    backend,
    document,
    interval: intervals[0],
    element(id) {
      return document.getElementById(id);
    },
    async poll() {
      await intervals[0]();
      await flushTasks();
    },
  };
}


function sessionCards(grid) {
  return grid.children.filter((child) => child.classList.contains("session-card"));
}


function selectedCard(grid) {
  return sessionCards(grid).find((card) => card.getAttribute("aria-pressed") === "true");
}


test("the 320x240 screen has no central state card", () => {
  function pixels(selector, property) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const rule = HTML.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
    assert.ok(rule, `missing CSS rule for ${selector}`);
    const value = rule[1].match(new RegExp(`${property}\\s*:\\s*(\\d+)px`));
    assert.ok(value, `missing ${property} for ${selector}`);
    return Number(value[1]);
  }

  assert.equal(pixels(".screen", "width"), 320);
  assert.equal(pixels(".screen", "height"), 240);
  assert.equal(
    pixels(".screen-header", "height") + pixels(".session-grid", "height") + pixels(".toolbar", "height"),
    240,
  );
  assert.doesNotMatch(HTML, /data-testid="state-card"/);
});


test("session counts choose the expected card-grid layout", async () => {
  const cases = [
    [0, "empty", 0, 1],
    [1, "wide", 1, 1],
    [4, "wide", 4, 4],
    [5, "medium", 5, 5],
    [6, "medium", 6, 6],
    [7, "dense", 7, 7],
    [8, "dense", 8, 8],
    [9, "dense", 7, 8],
  ];
  for (const [count, layout, cardCount, childCount] of cases) {
    const ui = await runUI(makeSessions(count));
    const grid = ui.element("session-grid");
    assert.ok(grid.classList.contains(layout), `${count} sessions should use ${layout}`);
    assert.equal(sessionCards(grid).length, cardCount, `${count} sessions card count`);
    assert.equal(grid.children.length, childCount, `${count} sessions grid child count`);
  }
});


test("every card exposes agent, project, state and message", async () => {
  const ui = await runUI(makeSessions(2));
  const cards = sessionCards(ui.element("session-grid"));
  assert.equal(cards.length, 2);
  assert.match(cards[0].getAttribute("aria-label"), /Codex:project-0#0: Working: message-0/);
  assert.match(cards[0].textContent, /Cxproject-0⚡Workingmessage-0/);
  assert.match(cards[1].getAttribute("aria-label"), /Claude:project-1#1: Done: message-1/);
});


test("clicking a card makes Ack target that exact session", async () => {
  const ui = await runUI(makeSessions(6));
  const first = sessionCards(ui.element("session-grid"))[0];
  await first.click();
  assert.equal(selectedCard(ui.element("session-grid")).getAttribute("aria-label").startsWith("Codex:project-0"), true);

  await ui.element("ack-button").click();
  const ack = ui.backend.posts.find((payload) => payload.action === "ack");
  assert.deepEqual(ack, { action: "ack", event_id: "event-0" });
  assert.equal(ui.backend.sessions.some((item) => item.session_id === "session-0"), false);
});


test("a newly arrived Approval card automatically receives focus", async () => {
  const ui = await runUI(makeSessions(3, ["processing", "done", "wait"]));
  const initial = selectedCard(ui.element("session-grid"));
  assert.match(initial.getAttribute("aria-label"), /^Codex:project-2#2: Waiting/);

  const updated = makeSessions(3, ["processing", "done", "wait"]);
  updated.push({
    session_id: "approval-new",
    source: "Claude:security#new",
    state: "approval",
    message: "needs permission",
    event_id: "approval-new-event",
    level: "attention",
    sequence: 20,
  });
  ui.backend.setSessions(updated);
  await ui.poll();
  assert.match(selectedCard(ui.element("session-grid")).getAttribute("aria-label"), /^Claude:security#new: Approval/);
});


test("overflow rotates a hidden session into the visible card set", async () => {
  const ui = await runUI(makeSessions(9));
  let grid = ui.element("session-grid");
  const overflow = grid.children.find((child) => child.classList.contains("session-overflow"));
  assert.ok(overflow);
  assert.equal(overflow.textContent, "+2");

  await overflow.click();
  grid = ui.element("session-grid");
  assert.match(selectedCard(grid).getAttribute("aria-label"), /project-6#6/);
  assert.equal(sessionCards(grid).some((card) => /project-6#6/.test(card.getAttribute("aria-label"))), true);
});


test("Mute and History controls update visible UI state", async () => {
  const ui = await runUI(makeSessions(2));
  await ui.element("mute-button").click();
  assert.equal(ui.backend.muted, true);
  assert.equal(ui.element("mute-label").textContent, "Muted");
  assert.equal(ui.element("mute-button").classList.contains("active"), true);

  await ui.element("more-button").click();
  assert.equal(ui.element("history-layer").classList.contains("visible"), true);
  await ui.element("history-back").click();
  assert.equal(ui.element("history-layer").classList.contains("visible"), false);
});


test("the Event Lab demo creates six independent session cards", async () => {
  const ui = await runUI([]);
  await ui.element("demo-button").click();
  const events = ui.backend.posts.filter((payload) => payload.action === "event");
  assert.equal(events.length, 6);
  assert.equal(new Set(events.map((payload) => payload.session_id)).size, 6);
  assert.equal(sessionCards(ui.element("session-grid")).length, 6);
  assert.equal(ui.element("session-grid").classList.contains("medium"), true);
  assert.match(selectedCard(ui.element("session-grid")).getAttribute("aria-label"), /^Claude:gadget#B2: Approval/);
});
