import assert from "node:assert/strict";
import { test } from "node:test";

import { imagesDisagree, pageIsOlder } from "./version.ts";

test("dev builds are never compared", () => {
  assert.equal(imagesDisagree("dev", "0.10.0"), false);
  assert.equal(imagesDisagree("0.10.0", "dev-abc"), false);
  assert.equal(imagesDisagree("0.10.0", undefined), false);
  assert.equal(imagesDisagree("0.9.0", "0.10.0"), true);
});

test("a page loaded before an update is older than the backend", () => {
  // Numeric, not text: "0.10.0" sorts before "0.9.0" as text.
  assert.equal(pageIsOlder("0.9.0", "0.10.0"), true);
  assert.equal(pageIsOlder("0.10.0", "0.10.1"), true);
  assert.equal(pageIsOlder("0.10.0", "0.9.0"), false);
  assert.equal(pageIsOlder("0.10.0", "0.10.0"), false);
  assert.equal(pageIsOlder("dev", "0.10.0"), false);
  assert.equal(pageIsOlder("0.9.0", "not-a-version"), false);
});
