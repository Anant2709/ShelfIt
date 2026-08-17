import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
  ensureNotificationPermission,
  loadReminderPrefs,
  saveReminderPrefs
} from "../reminders";

export default function Account() {
  const { user, logout, setStatus } = useAuth();
  const navigate = useNavigate();
  const [prefs, setPrefs] = useState(loadReminderPrefs);
  const [permission, setPermission] = useState(
    typeof Notification !== "undefined" ? Notification.permission : "unsupported"
  );

  const handleLogout = async () => {
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch (err) {
      setStatus(err.message);
    }
  };

  const updatePref = (key, value) => {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    saveReminderPrefs(next);
  };

  const enableNotifications = async () => {
    const result = await ensureNotificationPermission();
    setPermission(result);
    if (result === "granted") {
      setStatus("Local reminders enabled on this device.");
    } else if (result === "denied") {
      setStatus("Notifications are blocked in the browser settings.");
    } else if (result === "unsupported") {
      setStatus("This browser does not support notifications.");
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Account</h1>
        </div>
      </header>

      <section className="card">
        <h2>Signed in</h2>
        <p>
          <strong>{user?.username || user?.email}</strong>
        </p>
        {user?.email ? <p className="hint">{user.email}</p> : null}
        {user?.timezone ? (
          <p className="hint">Timezone: {user.timezone}</p>
        ) : null}
        <button type="button" className="ghost-button" onClick={handleLogout}>
          Sign out
        </button>
      </section>

      <section className="card">
        <h2>Install &amp; offline</h2>
        <p className="hint">
          Use your browser&apos;s Install / Add to Home Screen action for a
          standalone app. The service worker caches the shell so the UI still
          opens offline.
        </p>
      </section>

      <section className="card">
        <h2>Local reminders</h2>
        <p className="hint">
          Device-local only — no push server. Checks run while Shelf It is open
          (expiry within 2 days, unlogged meals today).
        </p>
        <p className="hint">Permission: {permission}</p>
        <button type="button" onClick={enableNotifications}>
          Allow notifications
        </button>
        <label className="diet-check">
          <input
            type="checkbox"
            checked={prefs.expiry}
            onChange={(event) => updatePref("expiry", event.target.checked)}
          />
          Expiry nudges
        </label>
        <label className="diet-check">
          <input
            type="checkbox"
            checked={prefs.meals}
            onChange={(event) => updatePref("meals", event.target.checked)}
          />
          Meal log nudges
        </label>
      </section>
    </div>
  );
}
