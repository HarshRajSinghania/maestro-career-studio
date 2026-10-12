/**
 * A template's own state, in words the gallery and the editor share. Pure, so `node --test` runs it
 * (`template-status.test.ts`).
 */

/**
 * The server's reason for a LaTeX template on a computer without TeX (`template_validation.REQUIRES_TEX`,
 * pinned equal by `test_frontend_resume_review.py`). Nothing is wrong with the template itself: the Needs
 * setup badge says what is missing.
 */
export const REQUIRES_TEX_REASON = "requires TeX (pdflatex not found)";

/** A template whose last check found a problem in the template, not a missing TeX. */
export function templateHasErrors(template: { last_error: string | null }): boolean {
  return Boolean(template.last_error) && template.last_error !== REQUIRES_TEX_REASON;
}

/** What the editor says when the preview can't be made because TeX is missing. */
export const NEEDS_TEX_WORDS =
  "This template needs TeX, which isn't installed on this computer, so its preview can't be made here.";

/**
 * How the server starts a refusal for a sample whose text overprints (`template_validation.OVERPRINT`).
 * It is the computer's TeX that is at fault (see `engines.PDFTEX_1_40_27_WARNING`), not the template's code.
 */
export const OVERPRINT_REASON_PREFIX = "text overprints:";

export const TEX_UPDATE_HEADING = "Your TeX installation needs an update";
export const TEMPLATE_ERROR_HEADING = "The template has an error. Fix the code, then update the preview.";
export const TEX_UPDATE_NOTE = "The template's code is not the problem.";

export type CompileErrorView = {
  /** `tex` when the fix is on this computer, `template` when it is in the template's code. */
  kind: "tex" | "template";
  heading: string;
  /** Said under the heading when the template itself is fine. */
  note: string | null;
  message: string;
  /** A shell command found in the message, to show on its own line. */
  command: string | null;
};

// A `tlmgr …` command (with a leading `sudo`), up to the end of the line, a `;`, `)` or a backtick.
const TLMGR_COMMAND = /(?:\bsudo\s+)?\btlmgr\s[^\n;)`]+/;

export function compileErrorView(error: string): CompileErrorView {
  if (!error.startsWith(OVERPRINT_REASON_PREFIX)) {
    return { kind: "template", heading: TEMPLATE_ERROR_HEADING, note: null, message: error, command: null };
  }
  const found = TLMGR_COMMAND.exec(error)?.[0].replace(/[\s.,]+$/, "") ?? null;
  return { kind: "tex", heading: TEX_UPDATE_HEADING, note: TEX_UPDATE_NOTE, message: error, command: found };
}
