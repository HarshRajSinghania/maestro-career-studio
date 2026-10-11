import assert from "node:assert/strict";
import { test } from "node:test";

import { keyFormatProblem, keysSavedText } from "./api-key-format.ts";

test("a key in the wrong shape is refused plainly, before any call", () => {
  assert.equal(keyFormatProblem("openai", "sk-proj-abc123", false), undefined);
  assert.equal(keyFormatProblem("gemini", "AIzaSyAbc", false), undefined);
  assert.equal(
    keyFormatProblem("openai", "hello", false),
    "An OpenAI API key starts with sk-. Check that you copied the whole key.",
  );
  assert.equal(
    keyFormatProblem("gemini", "sk-proj-abc", false),
    "A Gemini API key starts with AIza. Check that you copied the whole key.",
  );
  // Spaces from a copy are not the key's fault; an empty field removes the key.
  assert.equal(keyFormatProblem("openai", "  sk-abc  ", false), undefined);
  assert.equal(keyFormatProblem("openai", "", false), undefined);
  // A custom AI server's key has its own shape, whatever it is.
  assert.equal(keyFormatProblem("openai", "local-key", true), undefined);
});

test("a saved key is not called working: it is checked on first use", () => {
  assert.equal(keysSavedText({ openai_api_key: "sk-a" }), "API key saved. We'll check it on first use.");
  assert.equal(
    keysSavedText({ openai_api_key: "sk-a", gemini_api_key: "AIza" }),
    "API keys saved. We'll check them on first use.",
  );
  assert.equal(keysSavedText({ gemini_api_key: null }), "API key removed.");
  assert.equal(keysSavedText({ openai_api_key: null, gemini_api_key: null }), "API keys removed.");
});

test("Claude and OpenRouter keys have their own shapes", () => {
  assert.equal(keyFormatProblem("anthropic", "sk-ant-api03-abc", false), undefined);
  assert.equal(keyFormatProblem("openrouter", "sk-or-v1-abc", false), undefined);
  assert.equal(
    keyFormatProblem("anthropic", "sk-proj-abc", false),
    "A Claude API key starts with sk-ant-. Check that you copied the whole key.",
  );
  assert.equal(
    keyFormatProblem("openrouter", "sk-ant-abc", false),
    "An OpenRouter API key starts with sk-or-. Check that you copied the whole key.",
  );
});

test("a key pasted into the wrong field says which field it belongs in", () => {
  assert.equal(
    keyFormatProblem("openai", "sk-ant-api03-abc", false),
    "That looks like a Claude key. Paste it in the Claude API key field.",
  );
  assert.equal(
    keyFormatProblem("openai", "sk-or-v1-abc", false),
    "That looks like an OpenRouter key. Paste it in the OpenRouter API key field.",
  );
  // A custom AI server's key has whatever shape it has.
  assert.equal(keyFormatProblem("openai", "sk-ant-abc", true), undefined);
});

test("saved text counts every provider's key", () => {
  assert.equal(keysSavedText({ anthropic_api_key: "sk-ant-a" }), "API key saved. We'll check it on first use.");
  assert.equal(
    keysSavedText({ openai_api_key: "sk-a", openrouter_api_key: "sk-or-a", anthropic_api_key: null }),
    "API keys saved. We'll check them on first use.",
  );
  assert.equal(keysSavedText({ openrouter_api_key: null }), "API key removed.");
  assert.equal(keysSavedText({ anthropic_api_key: null, gemini_api_key: null }), "API keys removed.");
});
