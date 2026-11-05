import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, errorMessage } from "./api";
import { cloudToken } from "./auth";
import { CaseView } from "./CaseView";
import type { Actor, CaseAggregate, Queues } from "./types";

const queueLabels: Record<string, string> = {
  administrative: "Administrative", nurse: "Nurse clinical review", physician: "Physician review",
  peer_to_peer: "Peer to peer", letters: "Letters pending release",
};
const accounts = ["physician", "nurse", "intake", "pharmacist", "supervisor", "auditor", "admin"];

export default function App() {
  const [environment, setEnvironment] = useState("");
  const [token, setToken] = useState("");
  const [identity, setIdentity] = useState("physician");
  const [actor, setActor] = useState<Actor | null>(null);
  const [queues, setQueues] = useState<Queues>({});
  const [queue, setQueue] = useState("nurse");
  const [selected, setSelected] = useState<CaseAggregate | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [scenario, setScenario] = useState("G1");
  const [intakeJson, setIntakeJson] = useState("");
  const api = useMemo(() => new ApiClient(token), [token]);
  const refresh = useCallback(async () => { setQueues(await api.request<Queues>("/queues")); }, [api]);
  useEffect(() => {
    const controller = new AbortController();
    void new ApiClient("").request<{ environment: string }>("/health", undefined, controller.signal)
      .then(result => setEnvironment(result.environment))
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    void api.request<Actor>("/me", undefined, controller.signal).then(result => {
      if (!controller.signal.aborted) setActor(result);
      return api.request<Queues>("/queues", undefined, controller.signal);
    }).then(result => { if (!controller.signal.aborted) setQueues(result); })
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [api, token]);
  async function selectCase(identity: string) {
    setError(""); setBusy(true);
    try {
      const result = await api.request<CaseAggregate>(`/cases/${identity}`);
      setSelected(result);
      if (actor?.roles.some(role => ["nurse_reviewer", "physician_reviewer", "pharmacist_reviewer"].includes(role))) {
        await api.request(`/cases/${identity}/actions/start-review`, {});
      }
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  async function loadDemo() {
    setError(""); setBusy(true);
    try { setSelected(await api.request<CaseAggregate>(`/demo/${scenario}`, {})); await refresh(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  async function updated(value: CaseAggregate) {
    setSelected(value);
    try { await refresh(); } catch (failure) { setError(errorMessage(failure)); }
  }
  const development = environment === "development" || environment === "test";
  return (
    <>
      <a className="skip-link" href="#main">Skip to workbench</a>
      <header className="topbar"><div className="brand"><span className="brand-mark" aria-hidden="true">UM</span>
        <div><h1>Review with evidence</h1><p>Human supervised utilization management</p></div></div>
        {actor && <div><span>{actor.roles.join(", ").replaceAll("_", " ")}</span>
          <button className="secondary" onClick={() => { setToken(""); setActor(null); setSelected(null); setQueues({}); }}>Sign out</button></div>}
      </header>
      <div className="reference-notice">Synthetic reference implementation. Not for real patient care. Clinical decisions require validated policies and qualified reviewers.</div>
      <main id="main">
        {error && <p role="alert" className="error">{error}</p>}
        {!actor ? <section className="sign-in"><p className="eyebrow">Reviewer workbench</p><h2>Start a secure review session</h2>
          <p>Access depends on your authenticated role. Agents cannot make adverse determinations or release letters.</p>
          {development ? <form onSubmit={event => { event.preventDefault(); setError(""); setToken(`dev-${identity}`); }}>
            <label>Development identity<select value={identity} onChange={event => setIdentity(event.target.value)}>
              {accounts.map(account => <option value={account} key={account}>{account}</option>)}
            </select></label>
            <button>Sign in to synthetic workbench</button>
          </form> : <button disabled={!environment} onClick={() => {
            setError(""); void cloudToken().then(setToken).catch(failure => setError(errorMessage(failure)));
          }}>Sign in with Microsoft</button>}
        </section> : <div className="workbench">
          <aside aria-label="Work queues">
            <h2>Work queues</h2><p className="muted">Sorted by regulatory deadline.</p>
            <nav aria-label="Queue selection">{Object.entries(queueLabels).map(([key, label]) =>
              <button className={queue === key ? "queue active" : "queue"} aria-pressed={queue === key} key={key} onClick={() => setQueue(key)}>
                <span>{label}</span><strong>{queues[key]?.length || 0}</strong>
              </button>)}</nav>
            <button className="secondary" disabled={busy} onClick={() => void refresh().catch(failure => setError(errorMessage(failure)))}>Refresh queues</button>
            {development && <section className="demo"><h3>Synthetic intake</h3>
              <label>Golden scenario<select value={scenario} onChange={event => setScenario(event.target.value)}>
                {Array.from({ length: 10 }, (_, index) => `G${index + 1}`).map(value => <option key={value}>{value}</option>)}
              </select></label>
              <button disabled={busy} onClick={() => void loadDemo()}>Process synthetic request</button>
            </section>}
            <details><summary>Submit a channel fixture</summary><form onSubmit={event => {
              event.preventDefault(); setError(""); setBusy(true);
              let payload: unknown;
              try { payload = JSON.parse(intakeJson); } catch { setError("Enter valid JSON for an intake envelope."); setBusy(false); return; }
              void api.request<CaseAggregate>("/cases/intake", payload).then(value => updated(value))
                .catch(failure => setError(errorMessage(failure))).finally(() => setBusy(false));
            }}>
              <label>FHIR, X12, ePA, or fax envelope<textarea value={intakeJson} onChange={event => setIntakeJson(event.target.value)} required /></label>
              <button disabled={busy}>Submit for intake</button>
            </form></details>
          </aside>
          <section aria-label="Review workspace" className="workspace">
            <section aria-labelledby="queue-title"><header className="section-heading">
              <h2 id="queue-title">{queueLabels[queue]}</h2><span className="muted">{queues[queue]?.length || 0} cases</span>
            </header>
              <div className="queue-list">{(queues[queue] || []).length === 0 ? <p className="empty">No cases in this queue. {development ? "Process a synthetic request to begin." : "Refresh to check for new work."}</p> :
                queues[queue]?.map(item => <button className="case-card" disabled={busy} key={item.case_id} onClick={() => void selectCase(item.case_id)}>
                  <span><strong>{item.service_code}</strong><small>{item.line_of_business} / {item.urgency}</small></span>
                  <span><strong>{new Date(item.due_at).toLocaleString()}</strong><small>{Math.round(item.percent_elapsed)} percent of clock elapsed</small></span>
                </button>)}</div>
            </section>
            {selected ? <CaseView key={`${selected.case.case_id}:${selected.case.status}:${selected.agent_run_ids.length}:${selected.letters.map(letter => letter.letter_id).join(",")}`} current={selected} roles={actor.roles} api={api} updated={value => void updated(value)} /> :
              <section className="welcome"><h2>Evidence first. Humans decide.</h2><p>Select a case to review each criterion, open its exact source span, and take an authenticated action.</p>
                <div className="principles"><div><strong>Verified citations</strong><p>Unverifiable facts cannot satisfy criteria.</p></div><div><strong>Deterministic deadlines</strong><p>Calendar clocks continue across weekends.</p></div><div><strong>Human accountability</strong><p>Overrides and letter release are fully audited.</p></div></div>
              </section>}
          </section>
        </div>}
      </main>
      <footer>Reference data only. No real PHI. Automatic approval defaults to off.</footer>
    </>
  );
}
