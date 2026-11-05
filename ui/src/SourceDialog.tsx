import { useEffect, useRef } from "react";
import type { CitationView } from "./types";

export function SourceDialog({ source, close }: { source: CitationView; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  return (
    <dialog ref={dialog} onCancel={close} aria-labelledby="source-title">
      <header className="dialog-heading">
        <h2 id="source-title">Source: {source.citation.source_id}</h2>
        <button onClick={close} autoFocus>Close source</button>
      </header>
      <p>{source.citation.page ? `Page ${source.citation.page}` : source.citation.resource_path}</p>
      <pre className="source-text">
        {source.text.slice(0, source.highlight_start)}
        <mark>{source.text.slice(source.highlight_start, source.highlight_end)}</mark>
        {source.text.slice(source.highlight_end)}
      </pre>
    </dialog>
  );
}
