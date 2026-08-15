"""Tests for the review script.

This is the step that keeps the curated file human-authored: the app proposes,
this script lets a person commit. The behaviour that matters is that promotion
writes to the curated file, that rejection clears an entry so it is re-derived,
and that a correction goes straight to curated status.
"""

import json

import pytest

from app.core import config
from app.services import shelf_life
from app.services.learned_store import LearnedShelfLifeStore
from scripts import review_shelf_life as review


@pytest.fixture
def curated_file(tmp_path, monkeypatch):
    path = tmp_path / "shelf_life.json"
    path.write_text(json.dumps({"spinach": 4}), encoding="utf-8")
    monkeypatch.setattr(config.settings, "shelf_life_path", str(path))
    shelf_life.reset_dataset_cache()
    return path


@pytest.fixture
def store(db):
    return LearnedShelfLifeStore(session_factory=lambda: db)


def read_curated(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestPromote:
    def test_promoting_writes_into_the_curated_file(self, curated_file, store):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        assert review.promote(store, "baby spinach") is True
        assert read_curated(curated_file)["baby spinach"] == 4

    def test_promoting_preserves_existing_entries(self, curated_file, store):
        store.remember("baby spinach", days=4)
        review.promote(store, "baby spinach")
        assert read_curated(curated_file)["spinach"] == 4

    def test_promoting_marks_the_entry_confirmed(self, curated_file, store):
        store.remember("baby spinach", days=4)
        review.promote(store, "baby spinach")
        assert store.get("baby spinach").confirmed is True

    def test_promoted_value_then_resolves_as_curated(self, curated_file, store):
        """After promotion the item is served by tier 1, not tier 2."""
        store.remember("baby spinach", days=4)
        review.promote(store, "baby spinach")
        assert shelf_life.lookup_shelf_life_days("baby spinach", store=store) == (
            4,
            "dataset",
        )

    def test_promoting_an_unknown_name_reports_failure(self, curated_file, store):
        assert review.promote(store, "nope") is False

    def test_curated_file_is_written_sorted(self, curated_file, store):
        """Keeps promotion diffs small and readable in git."""
        for name in ["zucchini", "apple"]:
            store.remember(name, days=7)
            review.promote(store, name)
        assert list(read_curated(curated_file)) == [
            "apple",
            "spinach",
            "zucchini",
        ]

    def test_curated_file_ends_with_a_newline(self, curated_file, store):
        store.remember("apple", days=7)
        review.promote(store, "apple")
        assert curated_file.read_text(encoding="utf-8").endswith("\n")


class TestBulkApprove:
    def test_only_anchored_entries_are_promoted(self, curated_file, store, capsys):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        store.remember("saffron", days=365)
        review.command_approve_anchored(store)
        curated = read_curated(curated_file)
        assert "baby spinach" in curated
        assert "saffron" not in curated, "unanchored entries need real review"

    def test_reports_when_nothing_is_pending(self, curated_file, store, capsys):
        review.command_approve_anchored(store)
        assert "No anchored entries" in capsys.readouterr().out

    def test_already_confirmed_entries_are_skipped(self, curated_file, store):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        store.confirm("baby spinach")
        review.command_approve_anchored(store)
        assert "baby spinach" not in read_curated(curated_file)


class TestReject:
    def test_rejecting_removes_the_entry(self, curated_file, store):
        store.remember("coconut milk", days=5, anchor="milk", anchor_days=5)
        review.command_reject(store, "coconut milk")
        assert store.get("coconut milk") is None

    def test_rejecting_does_not_touch_the_curated_file(self, curated_file, store):
        store.remember("coconut milk", days=5)
        review.command_reject(store, "coconut milk")
        assert read_curated(curated_file) == {"spinach": 4}

    def test_rejecting_an_unknown_name_is_reported(self, curated_file, store, capsys):
        review.command_reject(store, "nope")
        assert "No learned entry" in capsys.readouterr().out


class TestCorrect:
    def test_correction_is_promoted_immediately(self, curated_file, store):
        """A human correction is the most valuable input available."""
        store.remember("coconut milk", days=5, anchor="milk", anchor_days=5)
        review.command_correct(store, "coconut milk", 730)
        assert read_curated(curated_file)["coconut milk"] == 730

    def test_correction_is_recorded_as_human_authored(self, curated_file, store):
        store.remember("coconut milk", days=5)
        review.command_correct(store, "coconut milk", 730)
        entry = store.get("coconut milk")
        assert entry.days == 730
        assert entry.model == "human-correction"
        assert entry.confirmed is True

    def test_correcting_an_unseen_item_still_works(self, curated_file, store):
        review.command_correct(store, "kimchi", 90)
        assert read_curated(curated_file)["kimchi"] == 90


class TestStaleListing:
    def test_drifted_entries_are_reported(self, curated_file, store, capsys):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        curated_file.write_text(json.dumps({"spinach": 9}), encoding="utf-8")
        shelf_life.reset_dataset_cache()
        review.command_stale(store)
        output = capsys.readouterr().out
        assert "baby spinach" in output
        assert "now 9d" in output

    def test_reports_when_nothing_has_drifted(self, curated_file, store, capsys):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        review.command_stale(store)
        assert "No entries have drifted" in capsys.readouterr().out


class TestListing:
    def test_pending_entries_are_listed(self, curated_file, store, capsys):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        review.command_list(store, show_all=False)
        output = capsys.readouterr().out
        assert "baby spinach" in output
        assert "like spinach (4d)" in output

    def test_unanchored_entries_say_so(self, curated_file, store, capsys):
        store.remember("saffron", days=365)
        review.command_list(store, show_all=False)
        assert "estimated, no reference" in capsys.readouterr().out

    def test_empty_store_reports_nothing_to_review(self, curated_file, store, capsys):
        review.command_list(store, show_all=False)
        assert "Nothing to review" in capsys.readouterr().out

    def test_all_includes_confirmed_entries(self, curated_file, store, capsys):
        store.remember("baby spinach", days=4)
        store.confirm("baby spinach")
        review.command_list(store, show_all=True)
        assert "confirmed" in capsys.readouterr().out

    def test_all_on_an_empty_store_is_reported(self, curated_file, store, capsys):
        review.command_list(store, show_all=True)
        assert "No learned entries yet" in capsys.readouterr().out


class TestCurationHelpers:
    def test_missing_curated_file_loads_as_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config.settings, "shelf_life_path", str(tmp_path / "absent.json")
        )
        assert review.load_curated() == {}

    def test_saving_creates_missing_directories(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "dir" / "shelf_life.json"
        monkeypatch.setattr(config.settings, "shelf_life_path", str(target))
        review.save_curated({"apple": 14})
        assert json.loads(target.read_text(encoding="utf-8")) == {"apple": 14}


class TestCli:
    def _run(self, monkeypatch, argv, store):
        monkeypatch.setattr("sys.argv", ["review_shelf_life"] + argv)
        monkeypatch.setattr(
            review, "LearnedShelfLifeStore", lambda *a, **k: store
        )
        review.main()

    def test_default_lists_pending(self, monkeypatch, curated_file, store, capsys):
        store.remember("baby spinach", days=4)
        self._run(monkeypatch, [], store)
        assert "baby spinach" in capsys.readouterr().out

    def test_approve_flag(self, monkeypatch, curated_file, store):
        store.remember("baby spinach", days=4)
        self._run(monkeypatch, ["--approve", "baby spinach"], store)
        assert "baby spinach" in read_curated(curated_file)

    def test_approve_anchored_flag(self, monkeypatch, curated_file, store):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        self._run(monkeypatch, ["--approve-anchored"], store)
        assert "baby spinach" in read_curated(curated_file)

    def test_reject_flag(self, monkeypatch, curated_file, store):
        store.remember("coconut milk", days=5)
        self._run(monkeypatch, ["--reject", "coconut milk"], store)
        assert store.get("coconut milk") is None

    def test_correct_flag(self, monkeypatch, curated_file, store):
        self._run(monkeypatch, ["--correct", "kimchi", "90"], store)
        assert read_curated(curated_file)["kimchi"] == 90

    def test_stale_flag(self, monkeypatch, curated_file, store, capsys):
        self._run(monkeypatch, ["--stale"], store)
        assert "drifted" in capsys.readouterr().out

    def test_all_flag(self, monkeypatch, curated_file, store, capsys):
        self._run(monkeypatch, ["--all"], store)
        assert "No learned entries yet" in capsys.readouterr().out
