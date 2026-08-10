// Harnisch fuer _DURATION_JS (#122 / Thema A) — kein Browser, aber echtes JS
// gegen einen DOM-Stub statt eines String-Greps auf den Quelltext. Schwester
// von `log_js_harness.js`.
//
// Zweck ist ein anderer als dort: hier wird nicht geprueft, ob der Code
// *laeuft*, sondern ob er **dasselbe formatiert wie Python**. Die drei
// Dauer-Regeln existieren nach Thema A zwangslaeufig zweimal — einmal im
// Renderer, einmal im Browser. Laufen sie auseinander, springt der Text beim
// ersten Tick sichtbar um, und niemand merkt es, weil beide Seiten fuer sich
// gruen sind.
//
// Liest den JS-Quelltext von stdin plus eine JSON-Aufgabe aus ARGV[2] und
// druckt die formatierten Werte als JSON.
"use strict";
const vm = require("vm");
const fs = require("fs");

const src = fs.readFileSync(0, "utf-8");
const aufgabe = JSON.parse(process.argv[2] || "{}");

// Minimaler DOM: der Ticker sucht Elemente und schreibt `textContent`. Fuer
// die Formatierungsfrage reicht eine leere Liste — geprueft werden die
// exportierten Funktionen, nicht der Durchlauf.
const sandbox = {
  document: { querySelectorAll: () => [] },
  setInterval: () => 0,
  clearInterval: () => {},
  Date: Date,
  Math: Math,
  console: console,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const fmt = sandbox.window.__bibiDauer;
if (!fmt) {
  console.log(JSON.stringify({ fehler: "window.__bibiDauer fehlt — der Ticker exportiert seine Formatierer nicht" }));
  process.exit(0);
}

const ergebnis = {};
for (const [art, werte] of Object.entries(aufgabe)) {
  ergebnis[art] = werte.map((v) => {
    // **Genau so, wie der Ticker sie aufruft** — er schneidet vorher ab, und
    // die Python-Seite tut dasselbe (`max(0, int(...))` bzw. `int(...)`).
    // Rohwerte durchzureichen pruefte eine Aufrufform, die es nicht gibt.
    if (art === "since") return fmt.dauer(v);
    if (art === "ago") return fmt.ago(Math.max(0, Math.trunc(v)));
    if (art === "until") return fmt.until(Math.trunc(v));
    return null;
  });
}
console.log(JSON.stringify(ergebnis));
