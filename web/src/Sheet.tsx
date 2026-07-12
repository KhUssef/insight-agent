import { ReactNode, useEffect } from "react";

// A bottom sheet for narrow layouts: a scrimmed panel that slides up from
// the bottom edge, closed by the scrim, the close button, or Escape.
export function Sheet({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-head">
          <span className="sheet-title">{title}</span>
          <button type="button" className="sheet-close" onClick={onClose} autoFocus>
            close
          </button>
        </header>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

// A full-viewport chart viewer: scrim, centered image, caption; closed by
// any click or Escape.
export function Lightbox({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={alt} onClick={onClose}>
      <img src={src} alt={alt} />
      <span className="lightbox-caption">{alt}</span>
    </div>
  );
}
