import React, { useEffect, useMemo, useState } from "react";
import {
  deleteItem,
  fetchInventory,
  getReminders,
  setExpiration,
  updateItem
} from "../api";
import { groupItemsByCategory } from "../categories";
import CategoryAccordion from "../components/CategoryAccordion";
import { useAuth } from "../context/AuthContext";
import { formatQuantity } from "../utils";

export default function Shelf() {
  const { setStatus } = useAuth();
  const [inventory, setInventory] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editQty, setEditQty] = useState(1);
  const [editUnit, setEditUnit] = useState("count");
  const [editExpiry, setEditExpiry] = useState("");

  const groups = useMemo(
    () => groupItemsByCategory(inventory),
    [inventory]
  );

  async function refresh() {
    try {
      const items = await fetchInventory();
      setInventory(items);
      const reminderRes = await getReminders(7);
      setReminders(reminderRes.items || []);
    } catch (err) {
      setStatus(err.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const startEdit = (item) => {
    setEditingId(item.id);
    setEditName(item.name);
    setEditQty(item.quantity);
    setEditUnit(item.unit || "count");
    setEditExpiry(item.expiration?.expiration_date || "");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
    setEditQty(1);
    setEditUnit("count");
    setEditExpiry("");
  };

  const saveEdit = async (itemId) => {
    setStatus("Saving changes...");
    try {
      await updateItem(itemId, {
        name: editName,
        quantity: Number(editQty),
        unit: editUnit
      });
      if (editExpiry) {
        await setExpiration(itemId, editExpiry);
      }
      setStatus("");
      cancelEdit();
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleDelete = async (itemId) => {
    setStatus("Removing item...");
    try {
      await deleteItem(itemId);
      setStatus("");
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Your kitchen</p>
          <h1>Shelf</h1>
        </div>
      </header>

      <section className="card">
        <h2>Upcoming expirations</h2>
        {reminders.length === 0 ? (
          <p className="hint">Nothing urgent this week.</p>
        ) : (
          <ul className="item-list">
            {reminders.map((item) => (
              <li key={item.id} className="item-pill">
                <strong>{item.name}</strong>
                <span className="hint">expires {item.expiration_date}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Inventory</h2>
        <p className="hint">Tap a category to expand.</p>
        {inventory.length === 0 ? (
          <p className="hint">No items yet. Scan something or add it manually.</p>
        ) : (
          <CategoryAccordion
            groups={groups}
            initiallyOpen={groups[0]?.id || null}
            renderItem={(item) =>
              editingId === item.id ? (
                <div className="inventory-row edit-row">
                  <input
                    type="text"
                    value={editName}
                    onChange={(event) => setEditName(event.target.value)}
                  />
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={editQty}
                    onChange={(event) => setEditQty(event.target.value)}
                  />
                  <select
                    value={editUnit}
                    onChange={(event) => setEditUnit(event.target.value)}
                  >
                    <option value="count">count</option>
                    <option value="ml">ml</option>
                    <option value="l">l</option>
                    <option value="g">g</option>
                    <option value="kg">kg</option>
                  </select>
                  <input
                    type="date"
                    value={editExpiry}
                    onChange={(event) => setEditExpiry(event.target.value)}
                  />
                  <div className="inventory-actions">
                    <button type="button" onClick={() => saveEdit(item.id)}>
                      Save
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={cancelEdit}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="inventory-row">
                  <div>
                    <strong>
                      {item.name} {formatQuantity(item)}
                    </strong>
                    <p className="hint">
                      {item.expiration?.expiration_date
                        ? `Expires ${item.expiration.expiration_date}`
                        : "No expiry set"}
                      {item.nutrition_source &&
                      item.nutrition_source !== "none" &&
                      item.calories_kcal != null
                        ? ` · ${item.calories_kcal} kcal (${item.nutrition_source})`
                        : ""}
                      {item.brand || item.product_name
                        ? ` · ${[item.brand, item.product_name].filter(Boolean).join(" ")}`
                        : ""}
                    </p>
                  </div>
                  <div className="inventory-actions">
                    <button type="button" onClick={() => startEdit(item)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={() => handleDelete(item.id)}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              )
            }
          />
        )}
      </section>
    </div>
  );
}
