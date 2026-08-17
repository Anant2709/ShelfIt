import React, { useEffect, useState } from "react";
import {
  deleteDietExtra,
  dietQuestionnaire,
  generateDietPlan,
  getDietAdherence,
  getDietPlan,
  getDietProfile,
  getDietProgress,
  getDietToday,
  listDietExtras,
  logDietExtra,
  logDietMeal,
  logDietWeighIn,
  saveDietProfile
} from "../api";
import { useAuth } from "../context/AuthContext";
import DietDashboard from "../diet/DietDashboard";
import DietWizard from "../diet/DietWizard";

export default function Diet() {
  const { setStatus } = useAuth();
  const [questions, setQuestions] = useState(null);
  const [profile, setProfile] = useState(null);
  const [hasProfile, setHasProfile] = useState(false);
  const [forceWizard, setForceWizard] = useState(false);
  const [today, setToday] = useState(null);
  const [plan, setPlan] = useState(null);
  const [adherence, setAdherence] = useState(null);
  const [progress, setProgress] = useState(null);
  const [extras, setExtras] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  async function loadDiet() {
    try {
      const [q, p, todayMeals, currentPlan, adh, prog, extraRows] =
        await Promise.all([
          dietQuestionnaire(),
          getDietProfile(),
          getDietToday(),
          getDietPlan(),
          getDietAdherence(7),
          getDietProgress(7),
          listDietExtras()
        ]);
      setQuestions(q);
      setProfile(p);
      setHasProfile(Boolean(p));
      setToday(todayMeals);
      setPlan(currentPlan);
      setAdherence(adh);
      setProgress(prog);
      setExtras(extraRows);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadDiet();
  }, []);

  const handleSave = async (payload) => {
    setBusy(true);
    setStatus("Saving diet profile...");
    try {
      const saved = await saveDietProfile(payload);
      setProfile(saved);
      setHasProfile(true);
      setForceWizard(false);
      setStatus(
        `Diet profile saved. Estimated ${saved.resolved_calorie_target} kcal/day.`
      );
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleGenerate = async (mode) => {
    setBusy(true);
    setStatus(
      mode === "ideal"
        ? "Building an ideal week, then checking the fridge..."
        : "Building this week's meals from your fridge..."
    );
    try {
      await generateDietPlan(mode);
      setStatus(
        mode === "ideal"
          ? "Ideal week ready. Missing ingredients are listed to buy."
          : "Week planned from what is on the shelf."
      );
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleLogMeal = async (meal, outcome, extra = {}) => {
    setBusy(true);
    try {
      await logDietMeal({
        slot: meal.slot,
        outcome,
        logged_date: meal.date,
        recipe_id: meal.recipe_id,
        title: meal.title,
        ...extra
      });
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleWeighIn = async (weightKg) => {
    setBusy(true);
    try {
      await logDietWeighIn({ weight_kg: weightKg });
      setStatus("Weigh-in saved.");
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleLogExtra = async (payload) => {
    setBusy(true);
    try {
      await logDietExtra(payload);
      setStatus("Extra intake logged.");
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteExtra = async (extraId) => {
    setBusy(true);
    try {
      await deleteDietExtra(extraId);
      setStatus("Extra intake removed.");
      await loadDiet();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (loading || !questions) {
    return (
      <div className="page">
        <p className="hint">Loading diet...</p>
      </div>
    );
  }

  const showWizard = !hasProfile || forceWizard;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Eat well from your fridge</p>
          <h1>Diet</h1>
        </div>
      </header>

      {showWizard ? (
        <DietWizard
          questions={questions}
          busy={busy}
          initial={forceWizard ? profile : null}
          onCancel={forceWizard ? () => setForceWizard(false) : null}
          onSave={handleSave}
        />
      ) : (
        <DietDashboard
          profile={profile}
          plan={plan}
          today={today}
          progress={progress}
          adherence={adherence}
          extras={extras}
          busy={busy}
          onGenerate={handleGenerate}
          onLogMeal={handleLogMeal}
          onWeighIn={handleWeighIn}
          onLogExtra={handleLogExtra}
          onDeleteExtra={handleDeleteExtra}
          onEditProfile={() => setForceWizard(true)}
        />
      )}
    </div>
  );
}
