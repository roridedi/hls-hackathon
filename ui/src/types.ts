export interface Actor { actor_id: string; roles: string[] }
export interface Clock { due_at: string; percent_elapsed: number; paused: boolean }
export interface QueueItem {
  case_id: string; status: string; line_of_business: string; service_code: string;
  urgency: string; due_at: string; percent_elapsed: number; confidence: string | null;
}
export type Queues = Record<string, QueueItem[]>;
export interface Citation {
  source_type: string; source_id: string; page: number | null;
  char_start: number | null; char_end: number | null; resource_path: string | null;
}
export interface Evidence {
  evidence_id: string; fact_type: string; normalized_value: unknown; unit: string | null;
  effective_date: string | null; citation: Citation; verified: boolean; verifier_note: string | null;
}
export interface Question { criterion_id: string; text: string; answer_type: string }
export interface Letter { letter_id: string; recipient: "member" | "provider"; text: string; status: string; approved_by: string | null }
export interface CaseAggregate {
  case: {
    case_id: string; member_token: string; channel: string; line_of_business: string;
    status: string; urgency: string; diagnoses: string[]; received_at: string;
    requesting_provider: { npi: string; specialty: string };
    service: { code: string; code_system: string; quantity: number; days_supply: number | null };
    clock: Clock;
  };
  coverage: { eligible: boolean; benefit_covered: boolean; pa_required: boolean; reason: string } | null;
  evidence: Evidence[];
  contradictions: { fact_type: string; evidence_ids: string[]; description: string }[];
  injection_sources: string[];
  evaluation: { results: { criterion_id: string; status: string; evidence_ids: string[]; rationale: string }[]; overall: string } | null;
  criteria_snapshot: { criteria_set_id: string; version: string; criteria: { id: string; description: string }[] } | null;
  recommendation: { outcome: string; confidence: string; reasons: string[]; open_questions: string[]; components: { explanation: string } } | null;
  documentation: { questions: Question[] } | null;
  reviewer_summary: { summary: string; evidence_ids: string[] } | null;
  determination: { outcome: string; plain_language_reason: string | null; override_rationale: string | null } | null;
  letters: Letter[];
  failures: string[];
  agent_run_ids: string[];
  automatic_approval: boolean;
}
export interface CitationView { citation: Citation; text: string; highlight_start: number; highlight_end: number }
export interface Summary { summary: string; evidence_ids: string[] }
