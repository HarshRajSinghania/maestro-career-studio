import assert from "node:assert/strict";
import { test } from "node:test";

import { compileErrorView, REQUIRES_TEX_REASON, TEMPLATE_ERROR_HEADING, templateHasErrors } from "./template-status.ts";

test("a template missing only TeX has no errors; any other reason is an error", () => {
  assert.equal(templateHasErrors({ last_error: REQUIRES_TEX_REASON }), false);
  assert.equal(templateHasErrors({ last_error: null }), false);
  assert.equal(templateHasErrors({ last_error: "" }), false);
  assert.equal(templateHasErrors({ last_error: "render error: Undefined control sequence" }), true);
});

test("a template's own error keeps the fix-the-code heading and shows the message as is", () => {
  const view = compileErrorView("render error: Undefined control sequence");
  assert.equal(view.kind, "template");
  assert.equal(view.heading, TEMPLATE_ERROR_HEADING);
  assert.equal(view.note, null);
  assert.equal(view.command, null);
  assert.equal(view.message, "render error: Undefined control sequence");
});

test("an overprint refusal blames the TeX install, not the template", () => {
  const error =
    "text overprints: words are drawn on top of each other in the PDF: pdfTeX 1.40.27 draws words on top of each other with many Type 1 fonts; update to pdfTeX 1.40.28+ (MacTeX 2026, or TeX Live 2025's final updates)";
  const view = compileErrorView(error);
  assert.equal(view.kind, "tex");
  assert.equal(view.heading, "Your TeX installation needs an update");
  assert.equal(view.message, error);
  assert.equal(view.command, null);
});

test("a tlmgr command in the message is lifted onto its own line", () => {
  const command = "sudo tlmgr --repository https://example.org/tlnet-final update --self --all";
  const view = compileErrorView(`text overprints: words are drawn on top of each other in the PDF: update TeX Live (${command}).`);
  assert.equal(view.kind, "tex");
  assert.equal(view.command, command);
  const bare = compileErrorView(`text overprints: x: run tlmgr update --self --all.`);
  assert.equal(bare.command, "tlmgr update --self --all");
});
