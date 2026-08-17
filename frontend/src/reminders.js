/** Local (non-push) reminders for expiry and meal logging. */

const STORAGE_KEY = "shelfit.reminders";

const defaults = {
  expiry: true,
  meals: true
};

export function loadReminderPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...defaults };
    return { ...defaults, ...JSON.parse(raw) };
  } catch {
    return { ...defaults };
  }
}

export function saveReminderPrefs(prefs) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

export async function ensureNotificationPermission() {
  if (!("Notification" in window)) {
    return "unsupported";
  }
  if (Notification.permission === "granted") {
    return "granted";
  }
  if (Notification.permission === "denied") {
    return "denied";
  }
  return Notification.requestPermission();
}

function notify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }
  try {
    new Notification(title, { body, icon: "/icon-192.png" });
  } catch {
    // Ignore environments that block Notification construction.
  }
}

let started = false;
let timer = null;

/**
 * Poll locally while the app is open. No push server — interview-safe.
 * `getShelf` / `getDietToday` are injected so this module stays API-light.
 */
export function startLocalReminders({ getReminders, getDietToday }) {
  if (started) return;
  started = true;

  const tick = async () => {
    const prefs = loadReminderPrefs();
    if (!prefs.expiry && !prefs.meals) return;
    if (!("Notification" in window) || Notification.permission !== "granted") {
      return;
    }

    try {
      if (prefs.expiry && getReminders) {
        const data = await getReminders();
        const due = (data?.items || data || []).filter((row) => {
          const days = row.days_remaining;
          return days != null && days <= 2;
        });
        if (due.length) {
          const names = due
            .slice(0, 3)
            .map((row) => row.name || row.item?.name)
            .filter(Boolean)
            .join(", ");
          notify(
            "Shelf It · expiring soon",
            names ? `${names} need attention.` : `${due.length} items expire soon.`
          );
        }
      }
      if (prefs.meals && getDietToday) {
        const today = await getDietToday();
        const unlogged = (today?.meals || []).filter((meal) => !meal.log);
        if (unlogged.length) {
          notify(
            "Shelf It · meal check-in",
            `${unlogged.length} planned meal${unlogged.length === 1 ? "" : "s"} still unlogged today.`
          );
        }
      }
    } catch {
      // Offline or unauthenticated — skip quietly.
    }
  };

  // First check after a short delay so login can settle; then hourly.
  timer = window.setTimeout(() => {
    tick();
    timer = window.setInterval(tick, 60 * 60 * 1000);
  }, 15_000);
}

export function stopLocalReminders() {
  if (timer) {
    clearTimeout(timer);
    clearInterval(timer);
    timer = null;
  }
  started = false;
}
