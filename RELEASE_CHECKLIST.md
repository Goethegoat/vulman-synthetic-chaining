# Public Release Checklist

Complete this checklist before publishing the folder as a public GitHub repository.

- [ ] All authors approve the artifact contents, README, and intended license.
- [ ] The CIFRE organization and relevant rights holders approve redistribution of each source file and result.
- [ ] An explicit software/data license is selected and added by the rights holder. No license is currently granted by this folder.
- [ ] Confirm the package contains only synthetic scenarios, synthetic tags, generated summaries, and authorized research code.
- [ ] Confirm no production rows, company names, asset identifiers, IP addresses, internal paths, tickets, secrets, or indirectly identifying configuration are present.
- [ ] Exclude the restricted source/derived databases, database-project connection files, and any data-preparation utility that refers to them.
- [ ] Search the staged files for local paths, usernames, organization identifiers, credentials, and unapproved third-party material.
- [ ] Re-run the unit tests and 50-seed tag smoke test from a clean Python environment.
- [ ] If publishing the full multiscale output, use an approved archival/release mechanism for large files; do not commit generated run directories to normal Git history.
- [ ] Add the final repository URL and version/commit identifier to the manuscript only after the public release is approved and created.
