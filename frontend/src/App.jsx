import React, { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import OfflineBanner from "./components/OfflineBanner";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { getDietToday, getReminders } from "./api";
import Account from "./pages/Account";
import Chat from "./pages/Chat";
import Diet from "./pages/Diet";
import Login from "./pages/Login";
import Scan from "./pages/Scan";
import Shelf from "./pages/Shelf";
import { startLocalReminders } from "./reminders";

function ProtectedRoutes() {
  const { user, authReady } = useAuth();

  useEffect(() => {
    if (!user) return undefined;
    startLocalReminders({
      getReminders: () => getReminders(3),
      getDietToday
    });
    return undefined;
  }, [user]);

  if (!authReady) {
    return (
      <div className="login-page">
        <div className="card auth-card">
          <h1>Shelf It</h1>
          <p className="hint">Checking your session...</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <>
      <OfflineBanner />
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Shelf />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/diet" element={<Diet />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/account" element={<Account />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<ProtectedRoutes />} />
      </Routes>
    </AuthProvider>
  );
}
