# Public Release Checklist

Repository: https://github.com/Goethegoat/vulman-synthetic-chaining

The initial synthetic artifact has been published. Use this checklist for follow-up releases and manuscript updates.

- [ ] Retain written author and CIFRE rights-holder approval for the exact source files and noncommercial license terms used in the public repository.
- [x] The checked-in package excludes the company database, pseudonymized database, project connection file, and database-preparation utility.
- [x] The package contains synthetic inputs/results only; generators, tests, and the README state the semantic and external-validity limits.
- [x] The 16 unit tests and 50-seed tag smoke test pass from the repository folder.
- [x] The full campaign regenerated 240 scenarios; the independent verifier replayed 1,200 policy decisions, 960 ablations, and 960 uncertainty decisions, plus 540 topology-control decisions.
- [ ] Periodically review third-party dependency versions and advisories; preserve a tested tag/commit for each manuscript revision.
- [ ] If a DOI or archived release is needed, create a versioned release and deposit only after rights review; do not commit the large generated scenario/decision directories to normal Git history.
- [x] Update the manuscript with the public repository URL and commit `acfc49ec83b6c73b7501e3cfb69b0798982fda1b`.
