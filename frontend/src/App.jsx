import { useCallback, useEffect, useRef, useState } from "react";
import UploadDropzone from "./components/UploadDropzone.jsx";
import MeetingResult from "./components/MeetingResult.jsx";
import HistoryList from "./components/HistoryList.jsx";
import AuthForm from "./components/AuthForm.jsx";
import { uploadMeeting, getMeeting, listMeetings, setAuthToken } from "./api.js";

export default function App() {
  const [activeMeeting, setActiveMeeting] = useState(null);
  const [history, setHistory] = useState([]);
  const [uploadError, setUploadError] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [currentUserEmail, setCurrentUserEmail] = useState(null);
  const [authRequired, setAuthRequired] = useState(false);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const pollRef = useRef(null);

  // Fetch API health config on load to check if auth is required
  useEffect(() => {
    async function checkAuthReq() {
      try {
        const res = await fetch("/api/health");
        if (res.ok) {
          const data = await res.json();
          setAuthRequired(!!data.auth_required);
        }
      } catch (err) {
        console.error("Health check failed:", err);
      }
    }
    checkAuthReq();
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await listMeetings());
    } catch {
      // history sidebar is non-critical; fail silently
    }
  }, []);

  useEffect(() => {
    refreshHistory();
  }, [refreshHistory, currentUserEmail]); // refresh history when logged-in user changes

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
    setUploadProgress(0);
    try {
      const meeting = await uploadMeeting(file, (progress) => {
        setUploadProgress(progress);
      });
      setActiveMeeting(meeting);
      refreshHistory();
      pollMeeting(meeting.id);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setIsUploading(false);
      setUploadProgress(0);
    }
  };

  const handleSelectHistory = async (id) => {
    clearInterval(pollRef.current);
    const meeting = await getMeeting(id);
    setActiveMeeting(meeting);
    if (meeting.status === "processing") pollMeeting(id);
  };

  const handleAuthSuccess = (email, token) => {
    setCurrentUserEmail(email);
    setShowAuthModal(false);
    setActiveMeeting(null);
    // history will refresh automatically via useEffect dependency on currentUserEmail
  };

  const handleLogout = () => {
    setAuthToken(null);
    setCurrentUserEmail(null);
    setShowAuthModal(false);
    setActiveMeeting(null);
    // history will refresh automatically via useEffect dependency on currentUserEmail
  };

  if (authRequired && !currentUserEmail) {
    return (
      <div className="app">
        <header className="app__header">
          <div className="app__header-left">
            <h1>
              Meeting <span className="app__accent">Summarizer</span>
            </h1>
            <p className="app__tagline">Audio in. Decisions and action items out.</p>
          </div>
        </header>
        <div className="app__body-auth-only">
          <AuthForm onAuthSuccess={handleAuthSuccess} showCancel={false} />
        </div>
        <style>{`
          .app {
            max-width: 1040px;
            margin: 0 auto;
            padding: 56px 24px 80px;
          }
          .app__header { 
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 36px;
            gap: 20px;
          }
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
          .app__body-auth-only {
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 350px;
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__header-left">
          <h1>
            Meeting <span className="app__accent">Summarizer</span>
          </h1>
          <p className="app__tagline">Audio in. Decisions and action items out.</p>
        </div>
        <div className="app__header-right">
          {currentUserEmail ? (
            <div className="app__user-status">
              <span className="app__user-email">{currentUserEmail}</span>
              <button onClick={handleLogout} className="app__auth-btn">
                Logout
              </button>
            </div>
          ) : (
            !authRequired && (
              <button onClick={() => setShowAuthModal(true)} className="app__auth-btn">
                Log In / Register
              </button>
            )
          )}
        </div>
      </header>

      <div className="app__body">
        <main className="app__main">
          {showAuthModal ? (
            <AuthForm
              onAuthSuccess={handleAuthSuccess}
              onCancel={() => setShowAuthModal(false)}
              showCancel={true}
            />
          ) : (
            <>
              <UploadDropzone
                onFileSelected={handleFileSelected}
                disabled={isUploading}
                isUploading={isUploading}
                progress={uploadProgress}
              />
              {uploadError && <p className="app__error">{uploadError}</p>}
              <div className="app__result-slot">
                <MeetingResult meeting={activeMeeting} />
              </div>
            </>
          )}
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
        .app__header { 
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 36px;
          gap: 20px;
        }
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
        .app__user-status {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .app__user-email {
          font-family: var(--font-mono);
          font-size: 0.82rem;
          color: var(--text-muted);
        }
        .app__auth-btn {
          background: transparent;
          color: var(--amber);
          border: 1px solid var(--amber-dim);
          padding: 8px 16px;
          border-radius: 8px;
          font-size: 0.85rem;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.2s, border-color 0.2s;
        }
        .app__auth-btn:hover {
          background: var(--amber-dim);
          border-color: var(--amber);
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
          .app__header { flex-direction: column; align-items: flex-start; }
          .app__body { grid-template-columns: 1fr; }
          .app__sidebar { position: static; }
        }
      `}</style>
    </div>
  );
}
