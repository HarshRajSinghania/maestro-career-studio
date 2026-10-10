import assert from "node:assert/strict";
import { test } from "node:test";

import { pdfOutputDetail } from "./pdf-output.ts";

const probe = (available: boolean, warning: string | null = null) => ({
  name: "pdflatex",
  available,
  version: null,
  path: null,
  reason: null,
  warning,
});

test("PDF output reads ready when TeX is there and sound", () => {
  assert.equal(pdfOutputDetail(probe(true)), "PDF creation is ready.");
});

test("without TeX it says a few templates need extra software", () => {
  assert.equal(
    pdfOutputDetail(probe(false)),
    "PDF creation is ready. A few templates need extra software to look their best.",
  );
});

test("a TeX the probe warns about is named, with its fix", () => {
  const warning =
    "pdfTeX 1.40.27 draws words on top of each other with many Type 1 fonts (e.g. Helvetica, Charter, Garamond, Fira); update to pdfTeX 1.40.28+ (MacTeX 2026, or TeX Live 2025's final updates)";
  assert.equal(
    pdfOutputDetail(probe(true, warning)),
    `PDF creation is ready, but your TeX needs an update: ${warning}.`,
  );
});
