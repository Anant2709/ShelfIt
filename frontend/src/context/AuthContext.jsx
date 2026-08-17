import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import {
  authProviders,
  login as apiLogin,
  logout as apiLogout,
  me,
  register as apiRegister
} from "../api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authError = params.get("auth_error");
    if (authError) {
      setStatus(authError);
      window.history.replaceState({}, "", window.location.pathname);
    }
    authProviders()
      .then((providers) => setGoogleEnabled(Boolean(providers.google)))
      .catch(() => setGoogleEnabled(false));
    me()
      .then((account) => setUser(account))
      .catch(() => setUser(null))
      .finally(() => setAuthReady(true));
  }, []);

  const login = async (identifier, password) => {
    const account = await apiLogin(identifier, password);
    setUser(account);
    setStatus("");
    return account;
  };

  const register = async (username, email, password) => {
    const account = await apiRegister(username, email, password);
    setUser(account);
    setStatus("");
    return account;
  };

  const loginDemo = async () => {
    setStatus("Opening the demo kitchen...");
    const account = await apiLogin("juhi", "shelfit");
    setUser(account);
    setStatus("");
    return account;
  };

  const logout = async () => {
    await apiLogout();
    setUser(null);
    setStatus("");
  };

  const value = useMemo(
    () => ({
      user,
      authReady,
      googleEnabled,
      status,
      setStatus,
      login,
      register,
      loginDemo,
      logout
    }),
    [user, authReady, googleEnabled, status]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}
