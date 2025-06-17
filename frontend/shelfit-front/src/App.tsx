import { useState, useEffect } from 'react'

interface HealthResponse{
  status: string;
}

function App() {
  const [apiStatus, setApiStatus] = useState<string | null>(null);

  useEffect(() => {
    fetch("http://localhost:8000/api/health")
      .then((res) => res.json() as Promise<HealthResponse>)
      .then((data) => setApiStatus(data.status))
      .catch(() => setApiStatus("down"));
  }, []); // empty deps array => run exactly once

  /* pick a colour based on result */
  const colour = apiStatus === "ok" ? "green" : apiStatus ? "red" : "gray";

  /* render -------------------------------------------------------- */
  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>ShelfIt (front-end skeleton)</h1>
      <p>
        Backend status:&nbsp;
        <span style={{ color: colour }}>
          {apiStatus ?? "contacting API..."}
        </span>
      </p>
    </main>
  );
}

export default App;