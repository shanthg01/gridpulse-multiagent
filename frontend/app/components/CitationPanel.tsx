"use client";

import type { Citation } from "../lib/types";
import { pdfUrl } from "../lib/api";

/** MVP citation panel: one card per retrieved chunk (doc title, page, RRF
 * score) with a link to the cached PDF. A full PDF.js viewer with live bbox
 * highlighting (chunk.bbox is captured for this) is the stretch goal left
 * for later -- this list is the accepted fallback per the build spec. */
export function CitationPanel({
  citations,
  activeMarker,
  onSelect,
}: {
  citations: Citation[];
  activeMarker: string | null;
  onSelect: (marker: string) => void;
}) {
  if (citations.length === 0) {
    return <p className="text-sm text-zinc-500">No citations for this answer.</p>;
  }

  return (
    <ul className="space-y-2">
      {citations.map((c) => {
        const isActive = activeMarker === c.marker;
        return (
          <li
            key={c.marker}
            id={`citation-${c.marker}`}
            onClick={() => onSelect(c.marker)}
            className={`cursor-pointer rounded-lg border p-3 text-sm transition-colors ${
              isActive
                ? "border-blue-500 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/40"
                : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-xs font-semibold text-blue-700 dark:text-blue-400">
                [{c.marker}]
              </span>
              <span className="text-xs text-zinc-500">rrf {c.rrf_score.toFixed(4)}</span>
            </div>
            <p className="mt-1 font-medium text-zinc-800 dark:text-zinc-100">{c.doc_title}</p>
            <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
              <span>page {c.page ?? "?"}</span>
              <a
                href={pdfUrl(c.document_id)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="font-medium text-blue-600 hover:underline dark:text-blue-400"
              >
                View PDF &#8599;
              </a>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
