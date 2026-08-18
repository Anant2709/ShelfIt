import React from "react";
import { useAuth } from "../context/AuthContext";

export default function StatusToast() {
  const { status, setStatus } = useAuth();
  if (!status) return null;

  return (
    <div className="status-toast" role="status" aria-live="polite">
      <p>{status}</p>
      <button
        type="button"
        className="status-toast-close"
        onClick={() => setStatus("")}
        aria-label="Dismiss notification"
      >
        Close
      </button>
    </div>
  );
}
