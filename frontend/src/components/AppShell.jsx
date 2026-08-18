import React from "react";
import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "Shelf", end: true, icon: "shelf" },
  { to: "/scan", label: "Scan", icon: "scan", emphasize: true },
  { to: "/diet", label: "Diet", icon: "diet" },
  { to: "/chat", label: "Chat", icon: "chat" },
  { to: "/account", label: "Account", icon: "account" }
];

function NavIcon({ name }) {
  if (name === "shelf") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M4 6h16M4 12h16M4 18h16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (name === "scan") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M8 4H5a1 1 0 0 0-1 1v3M16 4h3a1 1 0 0 1 1 1v3M8 20H5a1 1 0 0 1-1-1v-3M16 20h3a1 1 0 0 0 1-1v-3"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle
          cx="12"
          cy="12"
          r="3"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
      </svg>
    );
  }
  if (name === "diet") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 3c-2 3-4 5-4 8a4 4 0 0 0 8 0c0-3-2-5-4-8Zm0 13v5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (name === "chat") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M5 6h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 3v-3H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8" r="3.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path
        d="M5 19c1.5-3 4-4.5 7-4.5s5.5 1.5 7 4.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function AppShell() {
  return (
    <div className="shell">
      <aside className="rail" aria-label="Main">
        <div className="rail-sticky">
          <NavLink to="/" end className="rail-brand" aria-label="Shelf It">
            <img src="/icon-192.png" alt="" width="56" height="56" />
          </NavLink>
          <nav className="rail-nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  "rail-link",
                  item.emphasize ? "rail-link-scan" : "",
                  isActive ? "active" : ""
                ]
                  .filter(Boolean)
                  .join(" ")
              }
            >
              <span className="rail-icon">
                <NavIcon name={item.icon} />
              </span>
              <span className="rail-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
        </div>
      </aside>

      <div className="shell-main">
        <Outlet />
      </div>

      <nav className="bottom-nav" aria-label="Main">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              [
                "bottom-link",
                item.emphasize ? "bottom-link-scan" : "",
                isActive ? "active" : ""
              ]
                .filter(Boolean)
                .join(" ")
            }
          >
            <span className="rail-icon">
              <NavIcon name={item.icon} />
            </span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
