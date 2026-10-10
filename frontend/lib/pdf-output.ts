/**
 * The Getting started "PDF output" row's words, from the backend's pdflatex
 * probe (`services/engines`). Pure, so `node --test` runs it (`pdf-output.test.ts`).
 */

type PdflatexProbe = { available: boolean; warning: string | null };

export function pdfOutputDetail(pdflatex: PdflatexProbe): string {
  if (!pdflatex.available) {
    return "PDF creation is ready. A few templates need extra software to look their best.";
  }
  if (pdflatex.warning) {
    return `PDF creation is ready, but your TeX needs an update: ${pdflatex.warning}.`;
  }
  return "PDF creation is ready.";
}
