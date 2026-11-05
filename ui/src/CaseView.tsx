import { useState, type FormEvent } from "react";
import { ApiClient, errorMessage } from "./api";
import { SourceDialog } from "./SourceDialog";
import type { CaseAggregate, CitationView, Summary } from "./types";

export function CaseView({ current, roles, api, updated }: {
  current: CaseAggregate; roles: string[]; api: ApiClient; updated: (value: CaseAggregate) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<CitationView | null>(null);
  const [reason, setReason] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [override, setOverride] = useState("");
  const [outcome, setOutcome] = useState("APPROVED");
  const [quantity, setQuantity] = useState(current.case.service.quantity);
  const [duration, setDuration] = useState(current.case.service.days_supply || 1);
  const [questions, setQuestions] = useState(current.documentation?.questions.map(q => q.text).join("\n") || "");
  const [additional, setAdditional] = useState("");
  const [peerAt, setPeerAt] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Summary | null>(null);
  const [memberText, setMemberText] = useState(current.letters.find(l => l.recipient === "member")?.text || "");
  const [providerText, setProviderText] = useState(current.letters.find(l => l.recipient === "provider")?.text || "");
  const base = `/cases/${current.case.case_id}`;
  const clinical = roles.some(role => ["nurse_reviewer", "physician_reviewer", "pharmacist_reviewer"].includes(role));
  const licensed = roles.includes("physician_reviewer");
  const releaseRole = clinical || roles.includes("supervisor");
  const inReview = ["IN_CLINICAL_REVIEW", "IN_PHYSICIAN_REVIEW"].includes(current.case.status);

  async function action(name: string, body: unknown = {}) {
    setBusy(true); setError("");
    try { updated(await api.request<CaseAggregate>(`${base}/actions/${name}`, body)); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  async function openSource(identity: string) {
    setError("");
    try { setSource(await api.request<CitationView>(`${base}/evidence/${identity}/source`)); }
    catch (failure) { setError(errorMessage(failure)); }
  }
  async function exportAudit(extension: "json" | "pdf") {
    setError("");
    try { await api.download(`${base}/audit.${extension}`, `${current.case.case_id}.${extension}`); }
    catch (failure) { setError(errorMessage(failure)); }
  }
  async function ask(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { setAnswer(await api.request<Summary>(`${base}/copilot`, { question })); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  const citations = (identities: string[]) => identities.map(identity => {
    const item = current.evidence.find(e => e.evidence_id === identity);
    return <button className="citation" key={identity} onClick={() => void openSource(identity)}>
      {item?.citation.source_id || identity}{item?.citation.page ? `, page ${item.citation.page}` : ""}
    </button>;
  });
  return (
    <article aria-labelledby="case-title" className="case">
      <header className="section-heading">
        <div><p className="eyebrow">{current.case.line_of_business} / {current.case.channel}</p>
          <h2 id="case-title">Service {current.case.service.code}</h2>
          <p className="muted">Case {current.case.case_id}</p></div>
        <span className="badge">{current.case.status.replaceAll("_", " ")}</span>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      <section className="facts" aria-label="Request details">
        <div><strong>Member token</strong><span>{current.case.member_token}</span></div>
        <div><strong>Requesting provider</strong><span>{current.case.requesting_provider.npi}</span></div>
        <div><strong>Diagnoses</strong><span>{current.case.diagnoses.join(", ")}</span></div>
        <div><strong>Regulatory deadline</strong><span>{new Date(current.case.clock.due_at).toLocaleString()}</span></div>
        <div><strong>Urgency</strong><span>{current.case.urgency}</span></div>
        <div><strong>Clock</strong><span>{current.case.clock.paused ? "Paused by configured policy" : "Running. Weekends included."}</span></div>
      </section>
      {current.coverage && <p>{current.coverage.reason}</p>}
      {current.injection_sources.length > 0 && <p className="warning" role="status">
        Untrusted instructions detected in {current.injection_sources.join(", ")}. Source text was retained. No instruction grants permission to approve.
      </p>}
      {current.failures.map((failure, index) => <p role="alert" className="error" key={index}>{failure}</p>)}
      {current.recommendation && <section className="recommendation">
        <h3>{current.recommendation.outcome.replaceAll("_", " ")}</h3>
        <p><strong>{current.recommendation.confidence} confidence.</strong> {current.recommendation.components.explanation}</p>
        <p>{current.reviewer_summary?.summary}</p>
        {current.automatic_approval && <p>Approved by deterministic rules. Human letter release is still required.</p>}
      </section>}
      <h3>Criterion evidence</h3>
      <div className="table-scroll"><table>
        <caption className="sr-only">Criterion results and exact evidence citations</caption>
        <thead><tr><th scope="col">Criterion</th><th scope="col">Status</th><th scope="col">Rationale and sources</th></tr></thead>
        <tbody>{current.evaluation?.results.map(result => <tr key={result.criterion_id}>
          <th scope="row">{current.criteria_snapshot?.criteria.find(c => c.id === result.criterion_id)?.description || result.criterion_id}</th>
          <td><span className={`badge ${result.status.toLowerCase()}`}>{result.status.replaceAll("_", " ")}</span></td>
          <td>{result.rationale}<div>{citations(result.evidence_ids)}</div></td>
        </tr>)}</tbody>
      </table></div>
      {current.contradictions.map((contradiction, index) => <section className="warning" key={index}>
        <h3>Source contradiction: {contradiction.fact_type}</h3><p>{contradiction.description}</p>
        {citations(contradiction.evidence_ids)}
      </section>)}
      <details><summary>All extracted evidence, including rejected proposals</summary>
        <ul>{current.evidence.map(item => <li key={item.evidence_id}>
          <strong>{item.fact_type}</strong>: {JSON.stringify(item.normalized_value)} {item.unit}.
          {item.verified ? " Verified." : " Rejected."} {item.verifier_note}
          {citations([item.evidence_id])}
        </li>)}</ul>
      </details>
      {clinical && inReview && <section aria-labelledby="review-actions">
        <h3 id="review-actions">Clinical actions</h3>
        <button disabled={busy || current.recommendation?.outcome !== "APPROVE_RECOMMENDED"} onClick={() => void action("accept")}>Accept approval recommendation</button>
        <form onSubmit={event => {
          event.preventDefault();
          void action("determine", {
            outcome,
            ...(outcome === "APPROVED" || outcome === "PARTIALLY_APPROVED" ? { approved_quantity: quantity, approved_duration_days: duration } : {}),
            ...(reasonCode ? { specific_reason_code: reasonCode } : {}),
            ...(reason ? { plain_language_reason: reason } : {}),
            ...(override ? { override_rationale: override } : {}),
          });
        }}>
          <h4>Render determination</h4>
          <label>Outcome<select value={outcome} onChange={event => setOutcome(event.target.value)}>
            <option value="APPROVED">Approved</option>
            <option value="PARTIALLY_APPROVED" disabled={!licensed}>Partially approved. Physician required.</option>
            <option value="DENIED" disabled={!licensed}>Denied. Physician required.</option>
          </select></label>
          {(outcome === "APPROVED" || outcome === "PARTIALLY_APPROVED") && <div className="form-row">
            <label>Approved quantity<input type="number" min="0.01" step="any" value={quantity} onChange={event => setQuantity(Number(event.target.value))} required /></label>
            <label>Approved duration in days<input type="number" min="1" value={duration} onChange={event => setDuration(Number(event.target.value))} required /></label>
          </div>}
          <label>Specific reason code<input value={reasonCode} onChange={event => setReasonCode(event.target.value)} required={outcome !== "APPROVED"} /></label>
          <label>Plain language reason<textarea value={reason} onChange={event => setReason(event.target.value)} required={outcome !== "APPROVED"} /></label>
          <label>Override rationale<textarea value={override} onChange={event => setOverride(event.target.value)} /></label>
          <p className="muted">Changing the recommendation requires an explicit rationale. Clinical credentials are checked by the server.</p>
          <button disabled={busy}>Record human determination</button>
        </form>
        <form onSubmit={event => {
          event.preventDefault();
          void action("request-info", { questions: questions.split("\n").filter(text => text.trim()).map((text, index) => ({
            criterion_id: current.documentation?.questions[index]?.criterion_id || "additional_information", text, answer_type: "text",
          })) });
        }}>
          <h4>Request additional information</h4>
          <label>Questions. One per line<textarea value={questions} onChange={event => setQuestions(event.target.value)} required /></label>
          <button disabled={busy}>Approve information request</button>
        </form>
        <form onSubmit={event => { event.preventDefault(); void action("escalate", { reason }); }}>
          <h4>Escalation</h4>
          <label>Escalation reason<input value={reason} onChange={event => setReason(event.target.value)} minLength={3} required /></label>
          <button disabled={busy || current.case.status !== "IN_CLINICAL_REVIEW"}>Escalate to physician</button>
        </form>
        {licensed && current.case.status === "IN_PHYSICIAN_REVIEW" && <form onSubmit={event => {
          event.preventDefault(); void action("peer-to-peer", { scheduled_at: new Date(peerAt).toISOString(), reason });
        }}>
          <h4>Peer to peer review</h4>
          <label>Appointment time<input type="datetime-local" value={peerAt} onChange={event => setPeerAt(event.target.value)} required /></label>
          <label>Appointment reason<input value={reason} onChange={event => setReason(event.target.value)} minLength={3} required /></label>
          <button disabled={busy}>Schedule peer to peer</button>
        </form>}
      </section>}
      {licensed && current.case.status === "PEER_TO_PEER_SCHEDULED" && <button disabled={busy} onClick={() => void action("complete-peer", { reason: "Peer to peer review completed by the authenticated physician." })}>Complete peer to peer review</button>}
      {current.case.status === "PEND_MISSING_INFO" && (clinical || roles.includes("intake")) && <form onSubmit={event => {
        event.preventDefault(); void action("information", { sources: [{
          source_id: `additional_${crypto.randomUUID()}`, source_type: "DOCUMENT", pages: [additional],
        }] });
      }}>
        <h3>Additional source information</h3>
        <label>New clinical source text<textarea value={additional} onChange={event => setAdditional(event.target.value)} required /></label>
        <button disabled={busy}>Receive information and resume review</button>
      </form>}
      {current.case.status === "NOTIFICATION_PENDING_APPROVAL" && releaseRole && <section>
        <h3>Letters pending human release</h3>
        {current.letters.length === 0 ? <button disabled={busy} onClick={() => void action("draft-letters")}>Draft from approved templates</button> : <form onSubmit={event => {
          event.preventDefault(); void action("release", { confirm: true, member_text: memberText, provider_text: providerText });
        }}>
          <label>Member letter<textarea className="letter" value={memberText} onChange={event => setMemberText(event.target.value)} required /></label>
          <label>Provider letter<textarea className="letter" value={providerText} onChange={event => setProviderText(event.target.value)} required /></label>
          <p>Keep the specific reason, override rationale, and appeal rights. Member text must meet the eighth grade readability limit.</p>
          <label className="checkbox"><input type="checkbox" required />I reviewed both letters and approve their release.</label>
          <button disabled={busy}>Release approved letters</button>
        </form>}
      </section>}
      {clinical && <form onSubmit={event => void ask(event)}>
        <h3>Read only reviewer assistance</h3>
        <label>Question about verified evidence<input value={question} onChange={event => setQuestion(event.target.value)} required maxLength={2000} /></label>
        <button disabled={busy}>Find cited evidence</button>
        {answer && <div role="status"><p>{answer.summary}</p>{citations(answer.evidence_ids)}</div>}
      </form>}
      {current.case.status === "CLOSED" && <section><h3>Audit packet</h3>
        <button onClick={() => void exportAudit("json")}>Download audit JSON</button>
        <button onClick={() => void exportAudit("pdf")}>Download audit PDF</button>
      </section>}
      {source && <SourceDialog source={source} close={() => setSource(null)} />}
    </article>
  );
}
