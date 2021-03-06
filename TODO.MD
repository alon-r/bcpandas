# Master To-Do List

## Completed
- [x] **COME UP WITH A GOOD NAME!** (`pandasql` is already taken in PyPI)
- [x] Fix up `setup.py`
- [x] Stream BCP stdout to logs in real time asynchronously
- [x] Upload to PyPI
- [x] Upload to conda-forge
- [x] Set up CI/CD
- [x] Benchmarks vs. regular pandas methods
- [x] Find way to automate creation of temp SQL Server db for testing
- [x] Use pandas internal code to build new SQL tables, instead of just all NVARCHAR
- [x] Better docs
- [x] Remove SqlCmd and all references to it
- [x] Remove the deprecated `read_sql` and all references to it
- [x] Test inserting dataframe with (all) boolean values
- [x] Implement if dataframe is missing some database columns and/or the columns are out of order

## Open

### General
- [ ] `1.0.0` Release
- [ ] Write more tests for 100% code coverage
- [ ] Build in auto-retry with native pandas methods if BCP method fails

### Docs
- [ ] Add to docs required SQL permissions to run diff parts, and that bcpandas uses `INFORMATION_SCHEMA` SQL views
- [ ] Add to docs when/if NULL/NaNs are inserted and how
- [ ] Add section in docs detailing testing and Docker

### CI/CD
- [ ] Add the benchmarks to CI/CD
- [ ] Find way to use Linux containers on Windows OS in CI/CD


... and of course, anything marked `# TODO` in the code.
