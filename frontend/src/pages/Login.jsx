import React, { useState } from "react";
import { Navigate } from "react-router-dom";
import { googleSignInUrl } from "../api";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const {
    user,
    authReady,
    googleEnabled,
    demoEnabled,
    setStatus,
    login,
    register,
    loginDemo
  } = useAuth();
  const [authMode, setAuthMode] = useState("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [busy, setBusy] = useState(false);

  if (authReady && user) {
    return <Navigate to="/" replace />;
  }

  const handleAuth = async (event) => {
    event.preventDefault();
    if (authMode === "register") {
      if (!authUsername.trim() || !authEmail.trim() || !authPassword) {
        setStatus("Username, email, and password are all required.");
        return;
      }
      if (authPassword.length < 8) {
        setStatus("Password must be at least 8 characters.");
        return;
      }
    } else if (!authUsername.trim() || !authPassword) {
      setStatus("Enter your username or email, and your password.");
      return;
    }
    setBusy(true);
    setStatus(authMode === "login" ? "Signing in..." : "Creating account...");
    try {
      if (authMode === "login") {
        await login(authUsername, authPassword);
      } else {
        await register(authUsername, authEmail, authPassword);
      }
      setAuthPassword("");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDemo = async () => {
    setBusy(true);
    try {
      await loginDemo();
      setAuthPassword("");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!authReady) {
    return (
      <div className="login-page">
        <div className="login-hero card">
          <h1>Shelf It</h1>
          <p className="hint">Checking your session...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-hero">
        <p className="eyebrow">Fresh kitchen, less waste</p>
        <h1>Your food, tracked by AI</h1>
        <p className="login-sub">
          Scan groceries, know what expires, plan meals from what you already
          have, and ask the assistant what to cook tonight.
        </p>
      </div>

      <form className="card auth-card" onSubmit={handleAuth}>
        <h2>{authMode === "login" ? "Sign in" : "Create an account"}</h2>

        <label>
          {authMode === "login" ? "Username or email" : "Username"}
          <input
            type="text"
            value={authUsername}
            onChange={(event) => setAuthUsername(event.target.value)}
            autoComplete="username"
          />
        </label>
        {authMode === "register" && (
          <p className="hint">
            3–32 characters. Letters, numbers, and underscores only.
          </p>
        )}
        {authMode === "register" && (
          <label>
            Email
            <input
              type="email"
              value={authEmail}
              onChange={(event) => setAuthEmail(event.target.value)}
              autoComplete="email"
            />
          </label>
        )}
        <label>
          Password
          <input
            type="password"
            value={authPassword}
            onChange={(event) => setAuthPassword(event.target.value)}
            autoComplete={
              authMode === "login" ? "current-password" : "new-password"
            }
          />
        </label>
        {authMode === "register" && (
          <p className="hint">At least 8 characters.</p>
        )}

        <button type="submit" disabled={busy}>
          {authMode === "login" ? "Sign in" : "Create account"}
        </button>

        {googleEnabled && (
          <a className="google-button" href={googleSignInUrl()}>
            <svg
              className="google-button-logo"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                fill="#4285F4"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1Z"
              />
              <path
                fill="#34A853"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23Z"
              />
              <path
                fill="#FBBC05"
                d="M5.84 14.09A6.97 6.97 0 0 1 5.48 12c0-.73.13-1.43.36-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.94.46 3.77 1.18 5.39l3.66-2.3Z"
              />
              <path
                fill="#EA4335"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53Z"
              />
            </svg>
            Continue with Google
          </a>
        )}

        {demoEnabled && (
          <button
            type="button"
            className="ghost-button"
            disabled={busy}
            onClick={handleDemo}
          >
            Open the demo kitchen
          </button>
        )}

        <button
          type="button"
          className="link-button"
          onClick={() =>
            setAuthMode((mode) => (mode === "login" ? "register" : "login"))
          }
        >
          {authMode === "login"
            ? "Need an account? Register"
            : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
