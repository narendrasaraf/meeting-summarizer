import { useState } from "react";
import { loginUser, registerUser } from "../api.js";

export default function AuthForm({ onAuthSuccess, onCancel, showCancel }) {
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (isRegister) {
        const data = await registerUser(email, password);
        onAuthSuccess(email, data.access_token);
      } else {
        const data = await loginUser(email, password);
        onAuthSuccess(email, data.access_token);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card">
      <h2 className="auth-card__title">{isRegister ? "Create Account" : "Welcome Back"}</h2>
      <p className="auth-card__subtitle">
        {isRegister ? "Sign up to start saving and organizing your summaries" : "Log in to access your saved summaries"}
      </p>

      <form onSubmit={handleSubmit} className="auth-card__form">
        <div className="auth-card__field">
          <label htmlFor="auth-email">Email Address</label>
          <input
            id="auth-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            disabled={loading}
          />
        </div>

        <div className="auth-card__field">
          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            disabled={loading}
          />
        </div>

        {error && <p className="auth-card__error">{error}</p>}

        <div className="auth-card__actions">
          <button type="submit" className="auth-card__btn" disabled={loading}>
            {loading ? "Processing..." : isRegister ? "Sign Up" : "Log In"}
          </button>
          
          {showCancel && (
            <button
              type="button"
              className="auth-card__btn auth-card__btn--secondary"
              onClick={onCancel}
              disabled={loading}
            >
              Cancel
            </button>
          )}
        </div>
      </form>

      <div className="auth-card__toggle">
        <span>{isRegister ? "Already have an account?" : "Don't have an account?"}</span>{" "}
        <button
          type="button"
          onClick={() => {
            setIsRegister(!isRegister);
            setError(null);
          }}
          disabled={loading}
        >
          {isRegister ? "Log In" : "Register"}
        </button>
      </div>

      <style>{`
        .auth-card {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 32px;
          max-width: 420px;
          margin: 40px auto;
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }
        .auth-card__title {
          font-family: var(--font-display);
          font-size: 1.5rem;
          font-weight: 600;
          margin: 0 0 8px;
          text-align: center;
        }
        .auth-card__subtitle {
          font-size: 0.85rem;
          color: var(--text-muted);
          margin: 0 0 24px;
          text-align: center;
          line-height: 1.4;
        }
        .auth-card__form {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }
        .auth-card__field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .auth-card__field label {
          font-size: 0.78rem;
          font-family: var(--font-mono);
          color: var(--text-muted);
        }
        .auth-card__field input {
          background: var(--bg);
          border: 1px solid var(--border);
          color: var(--text);
          padding: 11px 14px;
          border-radius: 8px;
          font-size: 0.9rem;
          font-family: var(--font-body);
          transition: border-color 0.2s;
        }
        .auth-card__field input:focus {
          border-color: var(--amber);
          outline: none;
        }
        .auth-card__error {
          color: var(--red);
          font-family: var(--font-mono);
          font-size: 0.8rem;
          margin: 0;
          text-align: center;
        }
        .auth-card__actions {
          display: flex;
          gap: 12px;
          margin-top: 6px;
        }
        .auth-card__btn {
          flex: 1;
          background: var(--amber);
          color: var(--bg);
          font-weight: 600;
          border: none;
          padding: 12px;
          border-radius: 8px;
          font-size: 0.95rem;
          cursor: pointer;
          transition: opacity 0.2s, transform 0.1s;
        }
        .auth-card__btn:hover {
          opacity: 0.9;
        }
        .auth-card__btn:active {
          transform: scale(0.98);
        }
        .auth-card__btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .auth-card__btn--secondary {
          background: transparent;
          color: var(--text);
          border: 1px solid var(--border);
        }
        .auth-card__btn--secondary:hover {
          background: var(--surface-raised);
          border-color: var(--text-muted);
        }
        .auth-card__toggle {
          margin-top: 24px;
          text-align: center;
          font-size: 0.82rem;
          color: var(--text-muted);
          border-top: 1px solid var(--border);
          padding-top: 16px;
        }
        .auth-card__toggle button {
          background: none;
          border: none;
          color: var(--amber);
          font-weight: 600;
          cursor: pointer;
          padding: 0;
          text-decoration: underline;
        }
        .auth-card__toggle button:hover {
          color: var(--text);
        }
      `}</style>
    </div>
  );
}
