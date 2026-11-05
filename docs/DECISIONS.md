# Implementation decisions

## D001. Milestone sequencing

Decision. Implement the six milestones in order and run their offline acceptance tests before moving forward.

Alternatives. Build all surfaces before validation.

Reason. Early safety failures must block later work. GitHub authentication and cloud model credentials are absent in the implementation environment. Pull requests, hosted CI results, and live model acceptance remain external gates, not claimed successes.

Reversal. Authenticate GitHub and configure the documented cloud environment. Run the external acceptance commands and publish milestone pull requests before deployment.

## D002. Local persistence

Decision. PostgreSQL is the deployed database. SQLite is an explicit offline test and lightweight development backend, never a production fallback.

Alternatives. Require a running PostgreSQL server for every unit test.

Reason. The default suite must run without a network or cloud credentials. Both backends enforce immutable audit rows. PostgreSQL integration tests exercise the real transaction and outbox path.

Reversal. Set `UM_DATABASE_URL` and `UM_TEST_DATABASE_URL` to PostgreSQL URLs.

## D003. Clinical roles

Decision. Only physicians may make adverse determinations by default. Configuration may additionally authorize pharmacists for pharmacy cases. Nurses, administrators, and supervisors do not inherit physician credentials.

Alternatives. Allow every reviewer or administrator to deny requests.

Reason. Administrative privilege is not clinical licensure. Agents cannot transition cases, including agents claiming a human role.

Reversal. Compliance may configure a supported licensed role after validating its scope.

## D004. Regulatory clocks

Decision. Seed both synthetic lines with 72 hours expedited and 168 hours standard. Use elapsed calendar time in UTC, including weekends. Missing information does not pause a clock by default.

Alternatives. Business day arithmetic or a universal clock pause.

Reason. This matches the requested reference defaults. The synthetic pharmacy policy is not a statement that every pharmacy benefit is subject to CMS 0057 F. Jurisdiction and line specific configuration require compliance validation.

Reversal. Supply a validated configuration through `UM_CONFIG_PATH`. Each case stores its original SLA duration and due time.

## D005. Synthetic identities and development access

Decision. All checked in records are invented. Development bearer identifiers select a fixed role map. Production rejects development authentication and the fake model.

Alternatives. Accept roles supplied by a caller.

Reason. A caller must not be able to self assign physician credentials. The public development identifiers are not production secrets.

Reversal. Configure Entra tenant, audience, and group mappings for cloud use.

## D006. FHIR compatibility boundary

Decision. Accept the common R4 Claim preauthorization subset and validate its resources with the maintained Pydantic v2 `fhir.resources.R4B` models. Require one service per case and resolve patient and provider references inside the submitted bundle.

Alternatives. Pin the obsolete R4 library, which requires Pydantic v1, or claim complete PAS profile conformance.

Reason. The specified R4 library generation and the required Pydantic v2 stack are incompatible. The adapter explicitly supports only the demonstrated common subset. It is not a PAS certification implementation. Full R4 profile validation belongs at an integration gateway before real exchange.

Reversal. Replace the parser behind the normalization contract with a validated R4 PAS gateway adapter. The internal case contract does not change.

## D007. Channel transports and benefit uncertainty

Decision. X12 and ePA accept typed parsed fixtures. Fax accepts normalized form fields and source pages. Raw external transports remain phase 2 seams. Unknown benefits pend for administrative review rather than silently bypassing authorization.

Alternatives. Pretend parsed fixtures are production clearinghouse integrations or infer benefit coverage from missing configuration.

Reason. The specification scopes external transports out. Explicit uncertainty protects members and prevents unknown service codes from becoming automatic approvals.

Reversal. Implement authenticated transport adapters and replace the mock eligibility service through its interface.

## D008. Citation verification

Decision. Independent model verification is necessary but insufficient. A separate deterministic verifier resolves each exact source location and checks its normalized fact, value, unit, and effective date. Unsupported evidence remains in the audit as rejected and cannot enter a rules evaluation.

Alternatives. Trust an extractor supplied verified flag or accept a citation merely because the document exists.

Reason. A real location does not prove a clinical claim. The reference verifier recognizes the deliberately limited synthetic fact grammar and supported FHIR elements. Facts outside that vocabulary fail closed to unknown and human review.

Reversal. Add independently tested semantic verification adapters for new fact types before using new clinical content.

## D009. Injection handling and agent privileges

Decision. Detect instruction like text before any model call, retain the original source, log a source identifier event, and surface the detection to reviewers. Prompts delimit the source as untrusted data. Agents receive only read tool declarations and never a workflow handle.

Alternatives. Silently delete suspicious text or give agents tools that can approve cases.

Reason. Deletion damages the audit and may remove legitimate clinical facts. No model instruction can acquire authorization to change case state.

Reversal. A validated content safety adapter may add detection signals. It must preserve the original source and the deterministic privilege boundary.

## D010. Criteria semantics and confidence

Decision. Rules use verified evidence only. Missing, stale, future dated, unit incompatible, disputed, or unsupported facts are unknown. Explicit supported values below a threshold are not met. A clinical judgment flag always forces human review.

Confidence is HIGH only when the overall criteria are met, no contradiction exists, no judgment is required, and the evidence is neither exclusively from one unstructured document nor below the configured extraction score. Those source or score limitations produce MEDIUM. All other cases produce LOW. Components are stored with every recommendation. A model never chooses its own confidence.

Alternatives. Ask a model for a probability or treat missing facts as failures.

Reason. The formula is reproducible and uncertainty cannot become an adverse decision. The versioned synthetic lumbar MRI criterion uses a single alternative indication rule. Automatic approval additionally requires every individual criterion met, even if a future policy uses alternative top level logic.

Reversal. Version and validate a replacement formula and criteria. Preserve historical snapshots for replay.

## D011. Documentation and summaries

Decision. Extraction and documentation proposals run concurrently. After independent verification, deterministic reconciliation chooses the actual missing information questions from the criteria DSL. Reviewer summaries contain only stored criterion rationales and verified evidence references.

Alternatives. Send model drafted questions immediately or display unconstrained clinical narrative as a verified summary.

Reason. Parallel drafting reduces latency, but only verified facts can establish completeness. The final summary must not introduce an uncited clinical assertion. Information requests remain human approved.

Reversal. Add independently validated narrative claim verification before permitting free form clinical summaries.

## D012. Letters, translation, and delivery

Decision. Letters are assembled from validated determination fields and explicit synthetic templates. The letter agent cannot alter these factual boundaries. A human can edit the drafts, but release checks retain the specific reason, appeal rights, and override rationale. A readability estimate limits member letters to eighth grade language. Translation is an explicit adapter seam.

Alternatives. Release arbitrary model prose or send letters automatically.

Reason. A generated letter must not invent clinical facts or omit rights. Development delivery is a locally recorded outbox event, not an actual email, fax, or mailed letter. External delivery transports remain outside v1.

Reversal. Register a validated translation and delivery adapter, then retain human approval and exact sent content in the audit.

## D013. Human review actions

Decision. The server derives reviewer identity and role from authentication. A nurse may escalate to physician review but cannot make an adverse decision. Accepting an approval uses the requested quantity and days supply, or one day for a medical service with no requested duration. Overrides require a rationale.

Alternatives. Trust identity fields in a determination request or let administrators impersonate clinicians.

Reason. Queue routing is not a clinical credential. Immutable sources received later are appended under new identifiers so earlier citations remain reproducible.

Reversal. Replace the authentication provider without changing determination guards. Version any change to clinical scope and duration rules after validation.
