import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandglass import live_snapshot


class LiveSnapshotWriteTests(unittest.TestCase):
    """The recorded answer is what the audit reads instead of asking."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        live_snapshot.set_role("panel")
        live_snapshot._LAST_WRITE.clear()
        self.addCleanup(live_snapshot.set_role, "")
        self.addCleanup(live_snapshot._LAST_WRITE.clear)

    def _path(self) -> Path:
        return self.home / f"{live_snapshot.PREFIX}{os.getpid()}.json"

    def test_a_snapshot_we_cannot_read_is_not_replaced_with_one_reading(self):
        """A failed read is not empty state.

        `_read_own` treated every OSError as "no file", so a locked snapshot
        became `{}` and the next record wrote only the new reading over the
        top. The audit then reconciles against a truncated history, or skips
        because the file looks unlike a live writer, and skip is not pass.
        """
        path = self._path()
        original = json.dumps({
            "schema": 1,
            "process_id": os.getpid(),
            "role": "panel",
            "readings": {"quota": {"at": "2026-09-07T00:00:00+00:00",
                                   "value": {"kept": 1}}},
        })
        path.write_text(original, encoding="utf-8")
        reported = []
        real_read = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name.startswith(live_snapshot.PREFIX) and self.suffix == ".json":
                raise PermissionError("live-snapshot is locked")
            return real_read(self, *args, **kwargs)

        with patch.object(Path, "read_text", read_text), \
                patch("sandglass.live_snapshot.record_component_failure",
                      lambda name, exc: reported.append(name)):
            live_snapshot.record("quota", {"new": 2})

        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(reported, ["live_snapshot_write"])

    def test_a_snapshot_write_it_could_not_do_is_reported_rather_than_dropped(self):
        """A missing snapshot used to mean the panel had not answered.

        `_write_own` caught OSError and returned. `record` still stamped
        `_LAST_WRITE`, so the next call within the quiet interval skipped
        as if the value had been stored. The audit has nothing to read,
        and skip is not pass. Still not raised: this is bookkeeping beside
        an answer the caller already has.
        """
        recorded = []
        cleared = []
        live_snapshot.record("quota", {"kept": 1})
        path = self._path()
        original = path.read_text(encoding="utf-8")
        with patch("sandglass.live_snapshot.record_component_failure",
                   lambda name, exc: recorded.append(name)
                   if name == "live_snapshot_write" else None), \
                patch("sandglass.live_snapshot.clear_component_failure",
                      lambda name: cleared.append(name)
                      if name == "live_snapshot_write" else None):
            with patch("sandglass.live_snapshot.os.replace",
                       side_effect=OSError("disk full")):
                live_snapshot.record("quota", {"new": 2})
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(recorded, ["live_snapshot_write"])
            self.assertEqual(cleared, [])
            live_snapshot.record("quota", {"new": 2})
        self.assertEqual(cleared, ["live_snapshot_write"])
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["readings"]["quota"]["value"], {"new": 2})

    def test_an_unexpected_snapshot_failure_is_reported_rather_than_dropped(self):
        """The OSError path reports. The leftover Exception handler did not.

        `record` must not raise -- it is bookkeeping beside an answer the
        caller already has -- but a TypeError from the identity stamp used
        to return with nothing recorded, so a broken snapshot looked like
        a quiet interval.
        """
        recorded = []
        live_snapshot.record("quota", {"kept": 1})
        with patch(
            "sandglass.live_snapshot.process_started_at",
            side_effect=RuntimeError("measured"),
        ), patch(
            "sandglass.live_snapshot.record_component_failure",
            lambda name, exc: recorded.append(name)
            if name == "live_snapshot_write" else None,
        ):
            live_snapshot.record("quota", {"new": 2})
        self.assertEqual(recorded, ["live_snapshot_write"])

    def test_a_missing_snapshot_is_still_created(self):
        live_snapshot.record("quota", {"first": 1})
        stored = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(stored["schema"], 1)
        self.assertEqual(stored["readings"]["quota"]["value"], {"first": 1})


if __name__ == "__main__":
    unittest.main()
