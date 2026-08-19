import React, { useEffect, useMemo, useState } from "react";
import {
  deleteItem,
  fetchInventory,
  getReminders,
  getWasteReport,
  recordDisposition,
  setExpiration,
  undoDisposition,
  updateItem
} from "../api";
import { groupItemsByCategory } from "../categories";
import CategoryAccordion from "../components/CategoryAccordion";
import WastePatterns from "../components/WastePatterns";
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
  const [busyId, setBusyId] = useState(null);
  const [lastAction, setLastAction] = useState(null);
  const [wasteReport, setWasteReport] = useState(null);
  const [disposing, setDisposing] = useState(null);
  const [disposeQty, setDisposeQty] = useState("");

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
    try {
      setWasteReport(await getWasteReport(30));
    } catch {
      // Shelf still works if the report is unavailable.
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const startEdit = (item) => {
    setDisposing(null);
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

  const startDispose = (item, outcome) => {
    cancelEdit();
    setDisposing({
      id: item.id,
      outcome,
      remaining: item.quantity,
      unit: item.unit || "count",
      name: item.name
    });
    setDisposeQty(String(item.quantity));
  };

  const cancelDispose = () => {
    setDisposing(null);
    setDisposeQty("");
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
      setLastAction(null);
      cancelDispose();
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleDisposition = async () => {
    if (!disposing || busyId) return;
    const quantity = Number(disposeQty);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      setStatus("Enter how much to record.");
      return;
    }
    if (quantity > disposing.remaining) {
      setStatus("That is more than you have left.");
      return;
    }
    setBusyId(disposing.id);
    setStatus("");
    try {
      const result = await recordDisposition(disposing.id, {
        outcome: disposing.outcome,
        quantity
      });
      const verb = disposing.outcome === "consumed" ? "used" : "wasted";
      const gone = Boolean(result.item?.resolved_at);
      setLastAction({
        itemId: disposing.id,
        dispositionId: result.disposition.id,
        summary: gone
          ? `Recorded ${disposing.name} as ${verb}.`
          : `Recorded ${quantity} of ${disposing.name} as ${verb}. ${formatQuantity(result.item)} left.`
      });
      cancelDispose();
      await refresh();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const handleUndoLast = async () => {
    if (!lastAction || busyId) return;
    setBusyId(lastAction.itemId);
    try {
      await undoDisposition(lastAction.itemId, lastAction.dispositionId);
      setLastAction(null);
      setStatus("");
      await refresh();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusyId(null);
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

      <WastePatterns report={wasteReport} />

      <section className="card">
        <h2>Inventory</h2>
        <p className="hint">
          Tap a category to expand. Used and Wasted ask how much, then keep a
          log. Remove is only for something added by mistake.
        </p>
        {lastAction && (
          <p className="hint inventory-last-action">
            {lastAction.summary}{" "}
            <button
              type="button"
              className="link-button undo-button"
              disabled={Boolean(busyId)}
              onClick={handleUndoLast}
            >
              Undo
            </button>
          </p>
        )}
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
              ) : disposing?.id === item.id ? (
                <div className="inventory-row edit-row">
                  <div>
                    <strong>{item.name}</strong>
                    <p className="hint">
                      How much to mark as{" "}
                      {disposing.outcome === "consumed" ? "used" : "wasted"}?
                      Remaining {formatQuantity(item)}.
                    </p>
                  </div>
                  <label className="dispose-qty">
                    Amount
                    <input
                      type="number"
                      min={item.unit === "count" ? 1 : 0.01}
                      step={item.unit === "count" ? 1 : 0.01}
                      max={disposing.remaining}
                      value={disposeQty}
                      onChange={(event) => setDisposeQty(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          handleDisposition();
                        }
                      }}
                    />
                    {item.unit && item.unit !== "count" ? (
                      <span>{item.unit}</span>
                    ) : null}
                  </label>
                  <div className="inventory-actions">
                    <button
                      type="button"
                      disabled={Boolean(busyId)}
                      onClick={handleDisposition}
                    >
                      Record
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={Boolean(busyId)}
                      onClick={() =>
                        setDisposeQty(String(disposing.remaining))
                      }
                    >
                      All
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={cancelDispose}
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
                    <button
                      type="button"
                      disabled={Boolean(busyId)}
                      onClick={() => startDispose(item, "consumed")}
                    >
                      Used
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={Boolean(busyId)}
                      onClick={() => startDispose(item, "wasted")}
                    >
                      Wasted
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={Boolean(busyId)}
                      onClick={() => startEdit(item)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      disabled={Boolean(busyId)}
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
