const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api";

export async function fetchInventory() {
  const res = await fetch(`${API_BASE}/inventory/`);
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
  const res = await fetch(`${API_BASE}/inventory/scan/`, {
    method: "POST",
    body: formData
  });
  if (!res.ok) {
    throw new Error("Failed to scan item");
  }
  return res.json();
}

export async function labelItem(payload) {
  const res = await fetch(`${API_BASE}/inventory/label/`, {
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
  const res = await fetch(`${API_BASE}/inventory/`, {
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
  const res = await fetch(`${API_BASE}/inventory/${itemId}`, {
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
  const res = await fetch(`${API_BASE}/inventory/${itemId}/expiration`, {
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
  const res = await fetch(`${API_BASE}/inventory/reminders/?days=${days}`);
  if (!res.ok) {
    throw new Error("Failed to fetch reminders");
  }
  return res.json();
}

export async function deleteItem(itemId) {
  const res = await fetch(`${API_BASE}/inventory/${itemId}`, {
    method: "DELETE"
  });
  if (!res.ok) {
    throw new Error("Failed to delete item");
  }
  return res.json();
}

export async function sendChat(message) {
  const res = await fetch(`${API_BASE}/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });
  if (!res.ok) {
    throw new Error("Chat failed");
  }
  return res.json();
}
