"use client";

import { Fragment, type ReactNode } from "react";
import type { Citation } from "../lib/types";

// Matches **bold** spans and [C1]/[C12]/... citation markers so both can be
// picked out of plain-text answer lines in one pass.
const INLINE_RE = /(\*\*[^*]+\*\*|\[C\d+\])/g;

function renderInline(
  text: string,
  citationByMarker: Map<string, Citation>,
  onMarkerClick: (marker: string) => void,
  activeMarker: string | null,
  keyPrefix: string,
): ReactNode[] {
  return text.split(INLINE_RE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    const citation = citationByMarker.get(part);
    if (citation) {
      const isActive = activeMarker === citation.marker;
      return (
        <button
          key={key}
          type="button"
          onClick={() => onMarkerClick(citation.marker)}
          title={`${citation.doc_title} — p.${citation.page ?? "?"}`}
          className={`mx-0.5 inline-flex items-center rounded-full border px-1.5 py-0.5 align-middle text-xs font-medium transition-colors ${
            isActive
              ? "border-blue-600 bg-blue-600 text-white"
              : "border-blue-300 bg-blue-50 text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950/50 dark:text-blue-300 dark:hover:bg-blue-900/60"
          }`}
        >
          {part}
        </button>
      );
    }
    return <Fragment key={key}>{part}</Fragment>;
  });
}

/** Renders the agent's answer text (light markdown: "# Heading" / "**bold**")
 * with inline [C1]-style citation markers swapped for clickable chips that
 * highlight the matching entry in the citation panel. */
export function AnswerWithCitations({
  answer,
  citations,
  onMarkerClick,
  activeMarker,
}: {
  answer: string;
  citations: Citation[];
  onMarkerClick: (marker: string) => void;
  activeMarker: string | null;
}) {
  const citationByMarker = new Map(citations.map((c) => [`[${c.marker}]`, c]));
  const lines = answer.split("\n");

  return (
    <div className="space-y-2">
      {lines.map((line, i) => {
        if (line.trim() === "") return null;

        const heading = line.match(/^(#{1,6})\s+(.*)$/);
        if (heading) {
          const level = heading[1].length;
          const sizeClass =
            level === 1 ? "text-xl" : level === 2 ? "text-lg" : "text-base";
          return (
            <div
              key={i}
              className={`${sizeClass} mt-3 font-semibold text-zinc-900 first:mt-0 dark:text-zinc-50`}
            >
              {renderInline(heading[2], citationByMarker, onMarkerClick, activeMarker, `h${i}`)}
            </div>
          );
        }

        const bullet = line.match(/^[-*]\s+(.*)$/);
        if (bullet) {
          return (
            <div key={i} className="flex gap-2 pl-1 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
              <span aria-hidden>&bull;</span>
              <span>{renderInline(bullet[1], citationByMarker, onMarkerClick, activeMarker, `b${i}`)}</span>
            </div>
          );
        }

        return (
          <p key={i} className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
            {renderInline(line, citationByMarker, onMarkerClick, activeMarker, `p${i}`)}
          </p>
        );
      })}
    </div>
  );
}
