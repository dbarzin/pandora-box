# Detection regression corpus

`manifest.toml` lists samples put on a simulated device, with the verdict
expected from the scan. `tests/test_corpus.py` checks every sample:

- with the fake engine, in every test run;
- with real engines when `PANDORA_BOX_CORPUS_CONFIG` names a configuration
  file whose engines are installed (`pytest -m integration`).

## Adding a sample

- **Never commit real malware.** Malicious samples must be harmless test
  files (EICAR) generated at test time by a generator in `test_corpus.py`.
- Harmless samples are committed under `harmless/`. Keep them small and free
  of personal data.
- **Known false positives**: when an engine reports a harmless file, add it
  with `category = "false_positive"`, `expected = "clean"`, and a `reason`
  naming the engine, the signature and the issue. If the file cannot be
  shared, describe it and add only its SHA-256 in the reason.
