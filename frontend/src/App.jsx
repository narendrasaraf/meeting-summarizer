import { useCallback, useEffect, useRef, useState } from "react";
import UploadDropzone from "./components/UploadDropzone.jsx";
import MeetingResult from "./components/MeetingResult.jsx";
import HistoryList from "./components/HistoryList.jsx";
import { uploadMeeting, getMeeting, listMeetings } from "./api.js";

export default function App() {
  const [activeMeeting, setActiveMeeting] = useState(null);
  const [history, setHistory] = useState([]);
  const [uploadError, setUploadError] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const pollRef = useRef(null);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await listMeetings());
    } catch {
      // history sidebar is non-critical; fail silently
    }
  }, []);

  useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  const pollMeeting = useCallback(
    (id) => {
      clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const meeting = await getMeeting(id);
          setActiveMeeting(meeting);
          if (meeting.status !== "processing") {
            clearInterval(pollRef.current);
            refreshHistory();
          }
        } catch {
          clearInterval(pollRef.current);
        }
      }, 2500);
    },
    [refreshHistory]
  );

  useEffect(() => () => clearInterval(pollRef.current), []);

  const handleFileSelected = async (file) => {
    setUploadError(null);
    setIsUploading(true);
    try {
      const meeting = await uploadMeeting(file);
      setActiveMeeting(meeting);
      refreshHistory();
      pollMeeting(meeting.id);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setIsUploading(false);
    }
  };

  const handleSelectHistory = async (id) => {
    clearInterval(pollRef.current);
    const meeting = await getMeeting(id);
    setActiveMeeting(meeting);
    if (meeting.status === "processing") pollMeeting(id);
  };

  return (
    <div className="app">
      <header className="app__header">
        <h1>
          Meeting <span className="app__accent">Summarizer</span>
        </h1>
        <p className="app__tagline">Audio in. Decisions and action items out.</p>
      </header>

      <div className="app__body">
        <main className="app__main">
          <UploadDropzone onFileSelected={handleFileSelected} disabled={isUploading} />
          {uploadError && <p className="app__error">{uploadError}</p>}
          <div className="app__result-slot">
            <MeetingResult meeting={activeMeeting} />
          </div>
        </main>

        <aside className="app__sidebar">
          <HistoryList
            meetings={history}
            activeId={activeMeeting?.id}
            onSelect={handleSelectHistory}
          />
        </aside>
      </div>

      <style>{`
        .app {
          max-width: 1040px;
          margin: 0 auto;
          padding: 56px 24px 80px;
        }
        .app__header { margin-bottom: 36px; }
        .app__header h1 {
          font-family: var(--font-display);
          font-size: 2.1rem;
          font-weight: 600;
          margin: 0 0 8px;
          letter-spacing: -0.01em;
        }
        .app__accent { color: var(--amber); }
        .app__tagline {
          font-family: var(--font-mono);
          font-size: 0.85rem;
          color: var(--text-muted);
          margin: 0;
        }
        .app__body {
          display: grid;
          grid-template-columns: 1fr 240px;
          gap: 32px;
          align-items: start;
        }
        .app__main { display: flex; flex-direction: column; gap: 20px; min-width: 0; }
        .app__error {
          color: var(--red);
          font-family: var(--font-mono);
          font-size: 0.8rem;
          margin: 0;
        }
        .app__sidebar {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 16px;
          position: sticky;
          top: 24px;
        }
        @media (max-width: 760px) {
          .app__body { grid-template-columns: 1fr; }
          .app__sidebar { position: static; }
        }
      `}</style>
    </div>
  );
}
