import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function Dashboard() {
  const [user, setUser] = useState<{ email: string } | null>(null);

  useEffect(() => {
    api<{ email: string }>("/api/auth/me")
      .then(setUser)
      .catch(() => (window.location.href = "/login"));
  }, []);

  return <div>Welcome {user?.email ?? "..."}</div>;
}