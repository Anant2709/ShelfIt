import React, { useEffect, useRef, useState } from "react";
import { addItem, labelItem, scanItem } from "../api";
import { useAuth } from "../context/AuthContext";

export default function Scan() {
  const { setStatus } = useAuth();
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [expirationDate, setExpirationDate] = useState("");
  const [scanUnit, setScanUnit] = useState("count");
  const [scanQty, setScanQty] = useState(1);
  const [pendingLabel, setPendingLabel] = useState(null);
  const [labelInput, setLabelInput] = useState("");
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const fileInputRef = useRef(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [manualName, setManualName] = useState("");
  const [manualQty, setManualQty] = useState(1);
  const [manualExpiry, setManualExpiry] = useState("");
  const [manualUnit, setManualUnit] = useState("count");

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
  }, [cameraOn, setStatus]);

  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  useEffect(() => {
    if (!file) {
      setPreviewUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const clearPhoto = () => {
    setFile(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const startCamera = async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("This browser cannot open the camera. Upload a photo instead.");
        return;
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } }
        });
      } catch {
        stream = await navigator.mediaDevices.getUserMedia({ video: true });
      }
      streamRef.current = stream;
      setCameraOn(true);
      setStatus("Camera on. Capture a photo, then tap Scan & add.");
    } catch (err) {
      const name = err && err.name ? err.name : "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setStatus("Camera permission was denied. Allow the camera, or upload a photo.");
      } else {
        setStatus("Camera is unavailable. Upload a photo instead.");
      }
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
      setStatus("Photo captured. Check the preview, then tap Scan & add.");
    }, "image/jpeg");
  };

  const handleScan = async () => {
    if (!file) {
      setStatus("Select an image first.");
      return;
    }
    if (busy) return;
    setBusy(true);
    setStatus("Scanning...");
    try {
      const result = await scanItem(
        file,
        expirationDate || null,
        scanUnit,
        scanQty
      );
      clearPhoto();
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
      setPendingLabel(null);
      setLabelInput("");
      setExpirationDate("");
      setScanUnit("count");
      setScanQty(1);
      const created = result.created_items || [];
      if (result.status === "empty") {
        setStatus("Nothing recognised in that photo. Try a closer shot.");
      } else if (created.length === 1) {
        setStatus(`Added ${created[0].name}.`);
      } else {
        setStatus(`Added ${created.length} items.`);
      }
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleLabelSubmit = async () => {
    if (!pendingLabel) return;
    if (!labelInput) {
      setStatus("Enter a label for the item.");
      return;
    }
    if (busy) return;
    setBusy(true);
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
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleManualAdd = async () => {
    if (!manualName) {
      setStatus("Enter an item name.");
      return;
    }
    if (busy) return;
    setBusy(true);
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
      setStatus("Item added.");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Add groceries</p>
          <h1>Scan</h1>
        </div>
      </header>

      <section className="card">
        <h2>Scan item</h2>
        <p className="hint">
          Take a photo or upload one, check the preview, then scan. The model
          names what it sees; low confidence asks you to label.
        </p>

        <div className="camera-actions">
          {!cameraOn ? (
            <button type="button" onClick={startCamera}>
              Open camera
            </button>
          ) : (
            <>
              <button type="button" onClick={capturePhoto}>
                Capture
              </button>
              <button type="button" className="ghost-button" onClick={stopCamera}>
                Close camera
              </button>
            </>
          )}
        </div>

        {cameraOn && (
          <video
            ref={videoRef}
            className="camera-preview"
            playsInline
            muted
            onClick={(event) => event.currentTarget.play()}
          />
        )}

        <label>
          Or upload an image
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        {previewUrl ? (
          <div className="scan-preview">
            <img src={previewUrl} alt="Selected grocery photo" />
            <div className="scan-preview-meta">
              <p className="hint">
                Preview of {file?.name || "photo"}. If this is blurry or cropped,
                retake it before scanning.
              </p>
              <button
                type="button"
                className="ghost-button"
                onClick={clearPhoto}
                disabled={busy}
              >
                Remove photo
              </button>
            </div>
          </div>
        ) : (
          <p className="hint">No photo selected yet.</p>
        )}

        <div className="form-grid">
          <label>
            Quantity
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={scanQty}
              onChange={(event) => setScanQty(event.target.value)}
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
          <label>
            Expiration date (optional)
            <input
              type="date"
              value={expirationDate}
              onChange={(event) => setExpirationDate(event.target.value)}
            />
          </label>
        </div>

        <button type="button" onClick={handleScan} disabled={busy || !file}>
          {busy ? "Working..." : "Scan & add"}
        </button>

        {pendingLabel && (
          <div className="label-review">
            <div className="label-review-header">
              <strong>Needs a label</strong>
              <span className="hint">
                confidence {Math.round((pendingLabel.confidence || 0) * 100)}%
              </span>
            </div>
            <input
              type="text"
              placeholder="What is this?"
              value={labelInput}
              onChange={(event) => setLabelInput(event.target.value)}
            />
            <button type="button" onClick={handleLabelSubmit} disabled={busy}>
              Save label
            </button>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Manual add</h2>
        <div className="form-grid">
          <label>
            Name
            <input
              type="text"
              value={manualName}
              onChange={(event) => setManualName(event.target.value)}
            />
          </label>
          <label>
            Quantity
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={manualQty}
              onChange={(event) => setManualQty(event.target.value)}
            />
          </label>
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
        <button type="button" onClick={handleManualAdd} disabled={busy}>
          Add item
        </button>
      </section>
    </div>
  );
}
