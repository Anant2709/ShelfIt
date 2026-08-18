const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api";

function apiFetch(url, options = {}) {
  return fetch(url, { credentials: "include", ...options });
}

function errorFromResponse(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item.msg || item.message)
      .filter(Boolean)
      .join(" ");
  }
  return fallback;
}

export async function me() {
  const res = await apiFetch(`${API_BASE}/auth/me`);
  if (!res.ok) {
    throw new Error("Not signed in");
  }
  return res.json();
}

export async function authProviders() {
  const res = await apiFetch(`${API_BASE}/auth/providers`);
  if (!res.ok) {
    return { google: false, demo: false };
  }
  return res.json();
}

export function googleSignInUrl() {
  return `${API_BASE}/auth/google`;
}

export async function login(identifier, password) {
  const res = await apiFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, password })
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not sign in"));
  }
  return res.json();
}

export async function register(username, email, password, timezone) {
  const res = await apiFetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      email,
      password,
      timezone:
        timezone ||
        Intl.DateTimeFormat().resolvedOptions().timeZone ||
        "UTC"
    })
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not create an account"));
  }
  return res.json();
}

export async function logout() {
  const res = await apiFetch(`${API_BASE}/auth/logout`, { method: "POST" });
  if (!res.ok) {
    throw new Error("Could not sign out");
  }
  return res.json();
}

export async function fetchInventory() {
  const res = await apiFetch(`${API_BASE}/inventory/`);
  if (!res.ok) {
    throw new Error("Failed to fetch inventory");
  }
  return res.json();
}

export async function scanItem(file, expirationDate, unit, quantity) {
  const formData = new FormData();
  formData.append("file", file);
  if (expirationDate) {
    formData.append("expiration_date", expirationDate);
  }
  if (unit) {
    formData.append("unit", unit);
  }
  if (quantity) {
    formData.append("quantity", quantity);
  }
  const res = await apiFetch(`${API_BASE}/inventory/scan`, {
    method: "POST",
    body: formData
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(errorFromResponse(payload, "Failed to scan item"));
  }
  return res.json();
}

export async function labelItem(payload) {
  const res = await apiFetch(`${API_BASE}/inventory/label`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    throw new Error("Failed to label item");
  }
  return res.json();
}

export async function addItem(payload) {
  const res = await apiFetch(`${API_BASE}/inventory/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    throw new Error("Failed to add item");
  }
  return res.json();
}

export async function updateItem(itemId, payload) {
  const res = await apiFetch(`${API_BASE}/inventory/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    throw new Error("Failed to update item");
  }
  return res.json();
}

export async function setExpiration(itemId, expirationDate) {
  const res = await apiFetch(`${API_BASE}/inventory/${itemId}/expiration`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expiration_date: expirationDate })
  });
  if (!res.ok) {
    throw new Error("Failed to set expiration");
  }
  return res.json();
}

export async function getReminders(days = 7) {
  const res = await apiFetch(`${API_BASE}/inventory/reminders?days=${days}`);
  if (!res.ok) {
    throw new Error("Failed to fetch reminders");
  }
  return res.json();
}

export async function deleteItem(itemId) {
  const res = await apiFetch(`${API_BASE}/inventory/${itemId}`, {
    method: "DELETE"
  });
  if (!res.ok) {
    throw new Error("Failed to delete item");
  }
  return res.json();
}

export async function recordDisposition(itemId, payload) {
  const res = await apiFetch(`${API_BASE}/inventory/${itemId}/dispositions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || "Failed to record outcome");
  }
  return res.json();
}

export async function undoDisposition(itemId, dispositionId) {
  const res = await apiFetch(
    `${API_BASE}/inventory/${itemId}/dispositions/${dispositionId}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    throw new Error("Failed to undo");
  }
  return res.json();
}

export async function sendChat(message, conversationId = null) {
  const res = await apiFetch(`${API_BASE}/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId })
  });
  if (!res.ok) {
    throw new Error("Chat failed");
  }
  return res.json();
}

/**
 * Stream a reply, invoking `onEvent` for each server-sent event.
 *
 * Events are `token`, `action`, `done`, and `error`. Frames are reassembled here
 * because a chunk boundary can fall anywhere, including mid-JSON, so anything
 * after the last blank line is held back until the rest of it arrives.
 */
export async function streamChat(message, conversationId, onEvent) {
  const res = await apiFetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId })
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || "Chat failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      onEvent(JSON.parse(line.slice("data:".length).trim()));
    }
  }
}

export async function listConversations() {
  const res = await apiFetch(`${API_BASE}/chat/conversations`);
  if (!res.ok) {
    throw new Error("Failed to fetch conversations");
  }
  return res.json();
}

export async function getConversation(conversationId) {
  const res = await apiFetch(`${API_BASE}/chat/conversations/${conversationId}`);
  if (!res.ok) {
    throw new Error("Failed to fetch conversation");
  }
  return res.json();
}

export async function deleteConversation(conversationId) {
  const res = await apiFetch(`${API_BASE}/chat/conversations/${conversationId}`, {
    method: "DELETE"
  });
  if (!res.ok) {
    throw new Error("Failed to delete conversation");
  }
  return res.json();
}

export async function dietQuestionnaire() {
  const res = await apiFetch(`${API_BASE}/diet/questionnaire`);
  if (!res.ok) {
    throw new Error("Failed to load diet options");
  }
  return res.json();
}

export async function getDietProfile() {
  const res = await apiFetch(`${API_BASE}/diet/profile`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error("Failed to load diet profile");
  }
  return res.json();
}

export async function saveDietProfile(payload) {
  const res = await apiFetch(`${API_BASE}/diet/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not save diet profile"));
  }
  return res.json();
}

export async function getDietPlan() {
  const res = await apiFetch(`${API_BASE}/diet/plan`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error("Failed to load diet plan");
  }
  return res.json();
}

export async function generateDietPlan(mode = "pantry") {
  const res = await apiFetch(
    `${API_BASE}/diet/plan?mode=${encodeURIComponent(mode)}`,
    { method: "POST" }
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not generate a plan"));
  }
  return res.json();
}

export async function getDietToday() {
  const res = await apiFetch(`${API_BASE}/diet/today`);
  if (!res.ok) {
    throw new Error("Failed to load today's meals");
  }
  return res.json();
}

export async function logDietMeal(payload) {
  const res = await apiFetch(`${API_BASE}/diet/log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not log that meal"));
  }
  return res.json();
}

export async function getDietAdherence(days = 7) {
  const res = await apiFetch(`${API_BASE}/diet/adherence?days=${days}`);
  if (!res.ok) {
    throw new Error("Failed to load adherence");
  }
  return res.json();
}

export async function logDietWeighIn(payload) {
  const res = await apiFetch(`${API_BASE}/diet/weigh-ins`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not log that weigh-in"));
  }
  return res.json();
}

export async function getDietProgress(days = 7) {
  const res = await apiFetch(`${API_BASE}/diet/progress?days=${days}`);
  if (!res.ok) {
    throw new Error("Failed to load diet progress");
  }
  return res.json();
}

export async function listDietExtras(loggedDate) {
  const query = loggedDate
    ? `?logged_date=${encodeURIComponent(loggedDate)}`
    : "";
  const res = await apiFetch(`${API_BASE}/diet/extras${query}`);
  if (!res.ok) {
    throw new Error("Failed to load extra intake");
  }
  return res.json();
}

export async function logDietExtra(payload) {
  const res = await apiFetch(`${API_BASE}/diet/extras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not log that food"));
  }
  return res.json();
}

export async function deleteDietExtra(extraId) {
  const res = await apiFetch(`${API_BASE}/diet/extras/${extraId}`, {
    method: "DELETE"
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(errorFromResponse(detail, "Could not delete that entry"));
  }
  return res.json();
}
