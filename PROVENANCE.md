# Provenance of the results

Every results file in `results/` records the commit of the code that produced it (`provenance.git_commit`), and whether the working tree was clean (`git_dirty`).

This repository was published from a snapshot of the private research repository in which the experiments were developed and run. **The commits named in results files belong to that private history and cannot be looked up here.** The results files are unchanged copies, with one exception below: no results file was edited after its run in the research repository.

**The exception: seven files with one line replaced in this public copy.** Experiment 003's runner records each subject's `pip freeze`, which includes the editable install of this project with the research repository's address. In these seven public copies that address, and only that, is replaced by `<private research repository>`; the commit that follows it is kept. Each copy differs from its original in exactly that one line. The originals are unchanged in the research repository; their SHA-256, as committed there, is:

| File | SHA-256 of the original |
|---|---|
| `results/single_call/20261117T180204Z.json` | `e77f629f81a1fa8a6aff41184ad4d3266552e706f42189feca4b4cb360810cdb` |
| `results/single_call_linux/20261117T215146Z.json` | `45647c1d551a43085f35eb7b701d4bfd97ceb0f3b7d20c9c49f25ef7295daafe` |
| `results/single_call_linux/20261117T220513Z.json` | `5dc8204d7299fa14af1872304dbfc8a92fb514594476c7a024240b9de09d5a82` |
| `results/single_call_linux/20261117T220700Z.json` | `55f107e99b1eaf9bf178e4217e37fe2f8996a5fa6e002b8e3d8da0c11fdb7ad2` |
| `results/single_call_linux/20261117T220708Z.json` | `3fb0dc7bca4bad975bc20916d0b2747802e25af07adb5a04b847cf2721f39f7c` |
| `results/single_call_linux/20261117T220727Z.json` | `dee824b6d6b9c346308a02ececc836520b6540ba406b831684e7e823c8581c08` |
| `results/single_call_linux/20261117T220823Z.json` | `5769db34f9102a016711a1bf9e2a66943700517ad1c07de535ec9c44d0824e5b` |

What you can check here:

- **Every count against its write-up.** Each write-up in `docs/experiments/` links the results files it rests on.
- **The verdicts.** Every verdict comes from one function, `crashtest.checker.classify`. `python -m crashtest.results.reverdict` re-judges recorded trials, and `python -m crashtest.results.report_check` derives `report_matches_sink` for files written before contract v0.3.
- **Experiment 006 against the sink's own rows.** `python -m crashtest.experiment.model_retry_validate` checks the runner's records against an export of every PostgreSQL row for those orders (`results/model_retry/20261118-sink-export.json`).
- **The results themselves, by running the experiments.** The commands are in each write-up's "Reproduce" section; experiment 006 needs an Anthropic API key and costs money.
