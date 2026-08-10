// Harness fuer _LOG_JS (#112) — kein Browser, aber ein echter DOM-/Timer-Stub
// statt eines reinen String-Greps auf den Quelltext. Liest den JS-Quelltext
// von stdin, fuehrt ihn gegen Fake-document/-EventSource/-Timer aus und
// druckt das Ergebnis als JSON.
"use strict";
const vm = require("vm");
const fs = require("fs");

const src = fs.readFileSync(0, "utf-8");

function makeEl() {
  return {
    className: "",
    textContent: "",
    children: [],
    appendChild(child) { this.children.push(child); return child; },
    remove() {
      const i = box.children.indexOf(this);
      if (i >= 0) box.children.splice(i, 1);
    },
    set innerHTML(v) { if (v === "") this.children = []; },
    addEventListener() {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
  };
}

const box = makeEl();
const lvlSel = { value: "0" };   // debug -- alles durchlassen
const q = { value: "", oninput: null };

let now = 1_000_000;
const FakeDate = class extends Date {
  static now() { return now; }
};

let intervalFn = null;
const timeouts = [];

let esInstance = null;
class FakeEventSource {
  constructor(url) { this.url = url; esInstance = this; }
  close() {}
}

const sandbox = {
  document: {
    getElementById(id) {
      if (id === "log") return box;
      if (id === "lvl") return lvlSel;
      if (id === "q") return q;
      return makeEl();
    },
    createElement() { return makeEl(); },
    createTextNode(t) { return { nodeType: "text", text: t }; },
  },
  EventSource: FakeEventSource,
  Date: FakeDate,
  setInterval(fn) { intervalFn = fn; return 1; },
  setTimeout(fn, ms) { timeouts.push(fn); return timeouts.length; },
  console,
  Set,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const result = {};

// 1) Frisch verbunden, keine Zeilen -- vor Ablauf der Idle-Frist keine Marke.
sandbox.refreshIdle();
result.quiet_but_fresh_shows_nothing = box.children.length === 0;

// 2) 25s Stille vergangen -- die Idle-Marke muss erscheinen.
now += 25000;
intervalFn();
result.idle_marker_appears_after_25s = box.children.length === 1
  && box.children[0].className === "ln idle"
  && box.children[0].textContent.indexOf("Connected") === 0;

// 3) Eine echte Zeile kommt an -- die Marke muss sofort weichen.
esInstance.onmessage({ data: JSON.stringify({ level: "INFO", role: "worker", event: "tick" }) });
result.idle_marker_clears_on_message = box.children.length === 1
  && box.children[0].className !== "ln idle";

// 4) Danach wieder 25s Stille -- die Marke kommt zurueck (nicht nur beim ersten Mal).
now += 25000;
intervalFn();
result.idle_marker_returns_after_activity = box.children.some(c => c.className === "ln idle");

console.log(JSON.stringify(result));
