import { useEffect, useState } from "react";

const PRIORITY_COLOR = {
  high: "var(--red)",
  medium: "var(--amber)",
  low: "var(--teal)",
};

function fmtDuration(seconds) {
  if (!seconds && seconds !== 0) return null;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function fmtTime(seconds) {
  if (!seconds && seconds !== 0) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

export default function MeetingResult({ meeting }) {
  const [copiedIndex, setCopiedIndex] = useState(null);

  useEffect(() => {
    if (meeting && meeting.filename) {
      document.title = `${meeting.filename} - Meeting Summarizer`;
    } else {
      document.title = "Meeting Summarizer";
    }
  }, [meeting]);

  if (!meeting) return null;

  if (meeting.status === "processing") {
    return (
      <div className="result result--processing">
        <div className="spinner" aria-hidden="true" />
        <p>Transcribing &amp; summarizing "{meeting.filename}"…</p>
        <style>{styles}</style>
      </div>
    );
  }

  if (meeting.status === "failed") {
    return (
      <div className="result result--error">
        <p className="result__title">Processing failed</p>
        <p className="result__error-msg">{meeting.error_message}</p>
        <style>{styles}</style>
      </div>
    );
  }

  const duration = fmtDuration(meeting.duration_seconds);

  const handleExport = () => {
    const md = [
      `# Meeting Summary: ${meeting.filename}`,
      duration ? `**Duration:** ${duration}` : '',
      `**Date:** ${new Date(meeting.created_at).toLocaleString()}`,
      '',
      '## Summary',
      meeting.summary || 'No summary generated.',
      '',
      '## Key Decisions',
      meeting.key_decisions?.length 
        ? meeting.key_decisions.map(d => `- ${d}`).join('\n')
        : 'No explicit decisions detected.',
      '',
      '## Action Items',
      meeting.action_items?.length
        ? meeting.action_items.map(a => {
            const owner = a.owner || 'Unassigned';
            const due = a.due_date ? ` (due: ${a.due_date})` : '';
            const priority = a.priority ? ` [Priority: ${a.priority.toUpperCase()}]` : '';
            return `- [ ] **${a.task}** - ${owner}${due}${priority}`;
          }).join('\n')
        : 'No action items detected.',
      ''
    ].filter(line => line !== '').join('\n');

    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `${meeting.filename.replace(/\.[^/.]+$/, "")}_summary.md`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleCopyTask = (taskText, index) => {
    navigator.clipboard.writeText(taskText);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 1500);
  };

  return (
    <div className="result">
      <header className="result__header">
        <div>
          <h2>{meeting.filename}</h2>
          {duration && <span className="result__badge">{duration}</span>}
        </div>
        <button className="result__export-btn" onClick={handleExport}>
          Export Markdown
        </button>
      </header>

      <section className="result__section">
        <h3>Summary</h3>
        <p className="result__summary">{meeting.summary || "No summary generated."}</p>
      </section>

      <section className="result__section">
        <h3>Key decisions</h3>
        {meeting.key_decisions?.length ? (
          <ul className="result__list">
            {meeting.key_decisions.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        ) : (
          <p className="result__empty">No explicit decisions detected.</p>
        )}
      </section>

      <section className="result__section">
        <h3>Action items</h3>
        {meeting.action_items?.length ? (
          <ul className="result__actions">
            {meeting.action_items.map((a, i) => (
              <li key={i} className="result__action">
                <span
                  className="result__priority-dot"
                  style={{ background: PRIORITY_COLOR[a.priority] || "var(--text-muted)" }}
                  title={`Priority: ${a.priority}`}
                />
                <div>
                  <p className="result__action-task">
                    {a.task}
                    <button
                      className="result__copy-btn"
                      onClick={() => handleCopyTask(a.task, i)}
                      title="Copy task to clipboard"
                    >
                      {copiedIndex === i ? "Copied! ✓" : "📋"}
                    </button>
                  </p>
                  <p className="result__action-meta">
                    {a.owner || "Unassigned"}
                    {a.due_date ? ` · due ${a.due_date}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="result__empty">No action items detected.</p>
        )}
      </section>

      <details className="result__transcript">
        <summary>Full transcript</summary>
        {meeting.segments?.length ? (
          <div className="result__segments">
            {meeting.segments.map((seg, i) => (
              <div key={i} className="result__segment">
                <span className="result__segment-time">[{fmtTime(seg.start)} - {fmtTime(seg.end)}]</span>
                <span className="result__segment-text">{seg.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <p>{meeting.transcript}</p>
        )}
      </details>

      <style>{styles}</style>
    </div>
  );
}

const styles = `
  .result {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 28px 32px;
  }
  .result--processing, .result--error {
    text-align: center;
    padding: 48px 32px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }
  .spinner {
    width: 28px; height: 28px;
    border: 2px solid var(--border);
    border-top-color: var(--amber);
    border-radius: 50%;
    margin: 0 auto 16px;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result__title { color: var(--red); font-weight: 600; margin-bottom: 6px; }
  .result__error-msg { font-family: var(--font-mono); font-size: 0.78rem; }
  .result__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 20px;
  }
  .result__header h2 {
    font-family: var(--font-display);
    font-size: 1.15rem;
    margin: 0 0 4px;
    word-break: break-word;
  }
  .result__badge {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    color: var(--amber);
    background: var(--amber-dim);
    padding: 3px 9px;
    border-radius: 999px;
    white-space: nowrap;
    display: inline-block;
  }
  .result__export-btn {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    cursor: pointer;
    transition: all 0.2s ease;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }
  .result__export-btn:hover {
    background: var(--border);
    border-color: var(--text-muted);
  }
  .result__section { margin-bottom: 22px; }
  .result__section h3 {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 0 0 10px;
  }
  .result__summary { margin: 0; line-height: 1.6; }
  .result__list { margin: 0; padding-left: 20px; line-height: 1.7; }
  .result__empty { color: var(--text-muted); font-size: 0.88rem; margin: 0; }
  .result__actions { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
  .result__action { display: flex; gap: 10px; align-items: flex-start; }
  .result__priority-dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
  .result__action-task { margin: 0; font-weight: 500; display: flex; align-items: center; }
  .result__copy-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.75rem;
    margin-left: 8px;
    padding: 2px 6px;
    border-radius: 4px;
    color: var(--text-muted);
    border: 1px solid transparent;
    transition: all 0.2s ease;
  }
  .result__copy-btn:hover {
    color: var(--text);
    background: var(--border);
    border-color: var(--border);
  }
  .result__action-meta { margin: 2px 0 0; font-size: 0.78rem; color: var(--text-muted); font-family: var(--font-mono); }
  .result__transcript { margin-top: 24px; border-top: 1px solid var(--border); padding-top: 16px; }
  .result__transcript summary {
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .result__transcript p { line-height: 1.7; color: var(--text-muted); white-space: pre-wrap; margin-top: 12px; }
  .result__segments {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
  }
  .result__segment {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }
  .result__segment-time {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--amber);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .result__segment-text {
    font-size: 0.88rem;
    color: var(--text);
  }
`;
