import React, { useEffect, useState } from "react";
import {
  addItem,
  deleteItem,
  fetchInventory,
  getReminders,
  labelItem,
  scanItem,
  sendChat,
  setExpiration,
  updateItem
} from "./api";

export default function App() {
  const [inventory, setInventory] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [file, setFile] = useState(null);
  const [expirationDate, setExpirationDate] = useState("");
  const [scanUnit, setScanUnit] = useState("count");
  const [scanQty, setScanQty] = useState(1);
  const [pendingLabel, setPendingLabel] = useState(null);
  const [labelInput, setLabelInput] = useState("");
  const videoRef = React.useRef(null);
  const streamRef = React.useRef(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [manualName, setManualName] = useState("");
  const [manualQty, setManualQty] = useState(1);
  const [manualExpiry, setManualExpiry] = useState("");
  const [manualUnit, setManualUnit] = useState("count");
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editQty, setEditQty] = useState(1);
  const [editUnit, setEditUnit] = useState("count");
  const [editExpiry, setEditExpiry] = useState("");
  const [chatMessage, setChatMessage] = useState("");
  const [chatReply, setChatReply] = useState("");
  const [status, setStatus] = useState("");

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

  useEffect(() => {
    if (!cameraOn || !videoRef.current || !streamRef.current) return;
    const video = videoRef.current;
    video.srcObject = streamRef.current;
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => {
        setStatus("Tap the video to start the camera preview.");
      });
    }
  }, [cameraOn]);

  const handleScan = async () => {
    if (!file) {
      setStatus("Select an image first.");
      return;
    }
    setStatus("Scanning...");
    try {
      const result = await scanItem(file, expirationDate || null, scanUnit, scanQty);
      setFile(null);
      if (result.status === "needs_label") {
        setPendingLabel({
          imageId: result.image_id,
          suggestedLabel: result.suggested_label,
          confidence: result.confidence,
          quantity: Number(scanQty),
          unit: scanUnit,
          expirationDate: expirationDate || null
        });
        setLabelInput(
          result.suggested_label && result.suggested_label !== "unknown"
            ? result.suggested_label
            : ""
        );
        setStatus("Low confidence. Please label the item.");
        return;
      }
      setFile(null);
      setPendingLabel(null);
      setLabelInput("");
      setExpirationDate("");
      setScanUnit("count");
      setScanQty(1);
      setStatus("Item scanned.");
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleLabelSubmit = async () => {
    if (!pendingLabel) return;
    if (!labelInput) {
      setStatus("Enter a label for the item.");
      return;
    }
    setStatus("Saving label...");
    try {
      await labelItem({
        image_id: pendingLabel.imageId,
        label: labelInput,
        quantity: pendingLabel.quantity,
        unit: pendingLabel.unit,
        expiration_date: pendingLabel.expirationDate
      });
      setPendingLabel(null);
      setLabelInput("");
      setExpirationDate("");
      setScanUnit("count");
      setScanQty(1);
      setStatus("Item labeled.");
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleManualAdd = async () => {
    if (!manualName) {
      setStatus("Enter an item name.");
      return;
    }
    setStatus("Adding item...");
    try {
      await addItem({
        name: manualName,
        quantity: Number(manualQty),
        unit: manualUnit,
        expiration_date: manualExpiry || null
      });
      setManualName("");
      setManualQty(1);
      setManualExpiry("");
      setManualUnit("count");
      refresh();
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleChat = async () => {
    if (!chatMessage) return;
    setStatus("Thinking...");
    try {
      const result = await sendChat(chatMessage);
      setChatReply(result.reply);
      setChatMessage("");
      setStatus("");
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

  const formatQuantity = (item) => {
    const unit = item.unit || "count";
    if (unit === "count") {
      return `x${item.quantity}`;
    }
    return `${item.quantity} ${unit}`;
  };

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" }
      });
      streamRef.current = stream;
      setCameraOn(true);
    } catch (err) {
      setStatus("Camera access denied or unavailable.");
    }
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setCameraOn(false);
  };

  const capturePhoto = () => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const capturedFile = new File([blob], "camera-capture.jpg", {
        type: "image/jpeg"
      });
      setFile(capturedFile);
      stopCamera();
    }, "image/jpeg");
  };

  return (
    <div className="app">
      <header>
        <h1>Shelf It</h1>
        <p>Track groceries, get expiry reminders, and ask for recipes.</p>
      </header>

      {status && <div className="status">{status}</div>}

      <section className="card">
        <h2>Scan Item</h2>
        <input
          type="file"
          accept="image/*"
          capture="environment"
          onChange={(event) => setFile(event.target.files[0])}
        />
        <div className="camera-actions">
          {!cameraOn ? (
            <button onClick={startCamera}>Open Camera</button>
          ) : (
            <>
              <button onClick={capturePhoto}>Capture Photo</button>
              <button className="ghost-button" onClick={stopCamera}>
                Close Camera
              </button>
            </>
          )}
        </div>
        {cameraOn && (
          <video
            className="camera-preview"
            ref={videoRef}
            autoPlay
            playsInline
            muted
          />
        )}
        <input
          type="number"
          min="0.01"
          step="0.01"
          value={scanQty}
          onChange={(event) => setScanQty(event.target.value)}
        />
        <div className="form-grid">
          <label>
            Expiration date (optional)
            <input
              type="date"
              value={expirationDate}
              onChange={(event) => setExpirationDate(event.target.value)}
            />
          </label>
          <label>
            Unit
            <select
              value={scanUnit}
              onChange={(event) => setScanUnit(event.target.value)}
            >
              <option value="count">count</option>
              <option value="ml">ml</option>
              <option value="l">l</option>
              <option value="g">g</option>
              <option value="kg">kg</option>
            </select>
          </label>
        </div>
        <button onClick={handleScan}>Scan & Add</button>
        {pendingLabel && (
          <div className="label-review">
            <div className="label-review-header">
              <strong>Label needed</strong>
              <span>
                Confidence:{" "}
                {pendingLabel.confidence
                  ? `${(pendingLabel.confidence * 100).toFixed(1)}%`
                  : "n/a"}
              </span>
            </div>
            <input
              type="text"
              placeholder="Enter item label"
              value={labelInput}
              onChange={(event) => setLabelInput(event.target.value)}
            />
            <button onClick={handleLabelSubmit}>Save Label</button>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Manual Add</h2>
        <input
          type="text"
          placeholder="Item name"
          value={manualName}
          onChange={(event) => setManualName(event.target.value)}
        />
        <input
          type="number"
          min="0.01"
          step="0.01"
          value={manualQty}
          onChange={(event) => setManualQty(event.target.value)}
        />
        <div className="form-grid">
          <label>
            Unit
            <select
              value={manualUnit}
              onChange={(event) => setManualUnit(event.target.value)}
            >
              <option value="count">count</option>
              <option value="ml">ml</option>
              <option value="l">l</option>
              <option value="g">g</option>
              <option value="kg">kg</option>
            </select>
          </label>
          <label>
            Expiration date (optional)
            <input
              type="date"
              value={manualExpiry}
              onChange={(event) => setManualExpiry(event.target.value)}
            />
          </label>
        </div>
        <button onClick={handleManualAdd}>Add Item</button>
      </section>

      <section className="card">
        <h2>Inventory</h2>
        {inventory.length === 0 ? (
          <p>No items yet.</p>
        ) : (
          <ul>
            {inventory.map((item) => (
              <li key={item.id}>
                {editingId === item.id ? (
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
                      <button onClick={() => saveEdit(item.id)}>Save</button>
                      <button className="ghost-button" onClick={cancelEdit}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="inventory-row">
                    <span>
                      {item.name} {formatQuantity(item)}{" "}
                      {item.expiration?.expiration_date
                        ? `(expires ${item.expiration.expiration_date})`
                        : ""}
                    </span>
                    <div className="inventory-actions">
                      <button onClick={() => startEdit(item)}>Edit</button>
                      <button
                        className="ghost-button"
                        onClick={() => handleDelete(item.id)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Upcoming Expirations</h2>
        {reminders.length === 0 ? (
          <p>No reminders.</p>
        ) : (
          <ul>
            {reminders.map((item) => (
              <li key={item.id}>
                {item.name} expires on {item.expiration_date}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Chatbot</h2>
        <textarea
          placeholder="Ask for a recipe or storage advice..."
          value={chatMessage}
          onChange={(event) => setChatMessage(event.target.value)}
        />
        <button onClick={handleChat}>Ask</button>
        {chatReply && <p className="chat-reply">{chatReply}</p>}
      </section>
    </div>
  );
}
