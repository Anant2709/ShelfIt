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
    status,
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
        {status ? <div className="status">{status}</div> : null}

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
