import { BrowserRouter, Routes, Route, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import type { FormEvent } from "react";

/* --------- Types ------------------------------------------- */
interface HealthResponse {
  status: string;
}

interface TokenResponse {
  access_token: string;
}

/* --------- API helper --------------------------------------- */
async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem("token");
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };
  const res = await fetch(`http://localhost:8000${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json() as Promise<T>;
}

/* --------- Login page --------------------------------------- */
function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const data = await api<TokenResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      localStorage.setItem("token", data.access_token);
      navigate("/");
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message || "Login failed");
      } else {
        setError("Login failed");
      }
    }
  }

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem", maxWidth: 400 }}>
      <h2>Login</h2>
      <form onSubmit={handleSubmit}>
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <br />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <br />
        <button type="submit">Sign in</button>
        {error && <p style={{ color: "red" }}>{error}</p>}
      </form>
    </main>
  );
}

/* --------- Dashboard page ----------------------------------- */
function Dashboard() {
  const [apiStatus, setApiStatus] = useState<string | null>(null);

  useEffect(() => {
    api<HealthResponse>("/api/health")
      .then((data) => setApiStatus(data.status))
      .catch(() => setApiStatus("down"));
  }, []);

  const colour =
    apiStatus === "ok" ? "green" : apiStatus ? "red" : "gray";

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>ShelfIt Dashboard</h1>
      <p>
        Backend status:&nbsp;
        <span style={{ color: colour }}>
          {apiStatus ?? "contacting API..."}
        </span>
      </p>
    </main>
  );
}

/* --------- App root ----------------------------------------- */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/login" element={<Login />} />
      </Routes>
    </BrowserRouter>
  );
}