"""Offline evaluation harness for Argus (PLAN section 7).

  dataset.py   strict models of the case files (cases/*.json) and their validation against the corpus
  settings.py  the eval's own settings: provider, key and model ids only (no Firestore or Auth emulators)
  plain.py     the plain_gemini condition: one structured call, no library, no tools
  runner.py    runs the four conditions and records one CaseRecord per case, condition and repeat
  metrics.py   pure metric functions over CaseRecords; every metric carries its n
  report.py    results/<run_id>/results.json and report.md; Firestore publishing only on --publish
  run.py       the CLI: python -m argus_api.eval.run --conditions ...

No number from this harness appears anywhere until it has been measured and reviewed (PLAN section 7).
"""
