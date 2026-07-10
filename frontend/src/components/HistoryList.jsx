const STATUS_LABEL = {
  processing: "Processing",
  completed: "Done",
  failed: "Failed",
};

export default function HistoryList({ meetings, activeId, onSelect }) {
  return (
    <div className="history">
      <h3 className="history__title">History</h3>
      {meetings.length === 0 && <p className="history__empty">No meetings yet.</p>}
      <ul className="history__list">
        {meetings.map((m) => (
          <li key={m.id}>
            <button
              className={`history__item ${m.id === activeId ? "history__item--active" : ""}`}
              onClick={() => onSelect(m.id)}
            >
              <span className="history__filename">{m.filename}</span>
              <span className={`history__status history__status--${m.status}`}>
                {STATUS_LABEL[m.status] || m.status}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <style>{`
        .history { padding: 4px; }
        .history__title {
          font-family: var(--font-mono);
          font-size: 0.72rem;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: var(--text-muted);
          margin: 0 0 14px;
        }
        .history__empty { color: var(--text-muted); font-size: 0.85rem; }
        .history__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
        .history__item {
          width: 100%;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          background: transparent;
          border: 1px solid transparent;
          border-radius: 8px;
          padding: 10px 12px;
          color: var(--text);
          text-align: left;
          cursor: pointer;
          font-size: 0.85rem;
        }
        .history__item:hover { background: var(--surface); }
        .history__item--active { background: var(--amber-dim); border-color: var(--amber); }
        .history__filename {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          max-width: 160px;
        }
        .history__status {
          font-family: var(--font-mono);
          font-size: 0.68rem;
          flex-shrink: 0;
        }
        .history__status--completed { color: var(--teal); }
        .history__status--processing { color: var(--amber); }
        .history__status--failed { color: var(--red); }
      `}</style>
    </div>
  );
}
