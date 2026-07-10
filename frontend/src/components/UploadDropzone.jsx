import { useCallback, useRef, useState } from "react";

const ACCEPTED = ".mp3,.wav,.m4a,.mp4,.webm,.ogg,.flac";

// Deterministic pseudo-random bar heights so the idle waveform looks
// organic without re-randomizing on every render.
const BAR_HEIGHTS = Array.from({ length: 48 }, (_, i) => {
  const seed = Math.sin(i * 12.9898) * 43758.5453;
  return 18 + Math.abs(seed % 1) * 64;
});

export default function UploadDropzone({ onFileSelected, disabled }) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setIsDragging(false);
      if (disabled) return;
      const file = e.dataTransfer.files?.[0];
      if (file) onFileSelected(file);
    },
    [onFileSelected, disabled]
  );

  return (
    <div
      className={`dropzone ${isDragging ? "dropzone--active" : ""} ${disabled ? "dropzone--disabled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !disabled) inputRef.current?.click();
      }}
      aria-label="Upload meeting audio file"
    >
      <div className="dropzone__waveform" aria-hidden="true">
        {BAR_HEIGHTS.map((h, i) => (
          <span
            key={i}
            className="dropzone__bar"
            style={{ height: `${h}%`, animationDelay: `${(i % 12) * 0.07}s` }}
          />
        ))}
      </div>

      <p className="dropzone__title">Drop meeting audio here</p>
      <p className="dropzone__subtitle">
        or click to browse — MP3, WAV, M4A, MP4, WEBM, OGG, FLAC (max 50MB)
      </p>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFileSelected(file);
          e.target.value = "";
        }}
      />

      <style>{`
        .dropzone {
          position: relative;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: var(--surface);
          padding: 48px 32px 36px;
          text-align: center;
          cursor: pointer;
          transition: border-color 0.2s ease, background 0.2s ease;
          overflow: hidden;
        }
        .dropzone:hover:not(.dropzone--disabled) {
          border-color: var(--amber);
        }
        .dropzone--active {
          border-color: var(--amber);
          background: var(--amber-dim);
        }
        .dropzone--disabled {
          cursor: default;
          opacity: 0.6;
        }
        .dropzone__waveform {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 3px;
          height: 72px;
          margin-bottom: 20px;
        }
        .dropzone__bar {
          display: inline-block;
          width: 3px;
          min-height: 6px;
          border-radius: 2px;
          background: var(--amber);
          opacity: 0.55;
          animation: pulse 1.8s ease-in-out infinite;
        }
        .dropzone--disabled .dropzone__bar {
          animation-play-state: paused;
        }
        @keyframes pulse {
          0%, 100% { transform: scaleY(0.5); opacity: 0.35; }
          50% { transform: scaleY(1); opacity: 0.85; }
        }
        .dropzone__title {
          font-family: var(--font-display);
          font-size: 1.15rem;
          font-weight: 600;
          margin: 0 0 6px;
          color: var(--text);
        }
        .dropzone__subtitle {
          font-family: var(--font-mono);
          font-size: 0.78rem;
          color: var(--text-muted);
          margin: 0;
          letter-spacing: 0.01em;
        }
      `}</style>
    </div>
  );
}
