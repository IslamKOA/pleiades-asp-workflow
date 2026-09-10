# Public-release checklist

This file records the items that should be completed before the first permanent public deposit of the Pléiades ASP Workflow.

## Before depositing in Recherche Data Gouv

- [ ] Select the license for the original workflow code and populate `LICENSE`.
- [ ] Confirm the selected license with the relevant laboratory/institutional policy if required.
- [ ] Verify redistribution/attribution terms for the bundled `data/RAF20.tac` resource; remove it or replace it with an external acquisition step if necessary.
- [ ] Confirm that no protected Pléiades, Pléiades NEO, SPOT, Airbus DS, or DINAMIS image products are present in the archive.
- [ ] Confirm that ASP binaries/source code are not bundled in the archive; `asp-install` should continue retrieving ASP separately from the official ASP release source.
- [ ] Run the full automated test suite.
- [ ] Validate notebook JSON and version strings.
- [ ] Build the final wheel/source archive if distributed.
- [ ] Create the fixed Research Data Gouv deposit corresponding exactly to the manuscript/software release.
- [ ] Add the Research Data Gouv DOI to `README.md` and `CITATION.cff`.

## After the associated paper is published

- [ ] Add the final article citation and DOI to `README.md`.
- [ ] Add the article as the preferred citation in `CITATION.cff`.
- [ ] Update the manuscript Open Research Statement with the archived software DOI.
- [ ] Tag the exact software release used in the article and preserve it unchanged.
- [ ] Keep later development versions separate from the archived publication release.
