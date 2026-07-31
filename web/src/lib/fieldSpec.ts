/**
 * Declarative layout for the structured editor, with every field key checked against the
 * generated types (ADR-020).
 *
 * The point of `StringKeys<T>` is the build-time signal. If a field is renamed in
 * `SimReadyCaseDetailsStructured`, regenerating `types.gen.ts` turns the stale key here
 * into a type error. Without that, a renamed field silently becomes a blank input an
 * author cannot tell apart from an empty value, and the case saves with the real field
 * untouched — which is exactly the failure mode generation exists to prevent.
 *
 * Labels are written out rather than derived from the key so they can read like clinical
 * prose ("Aggravating / alleviating") instead of snake_case.
 */

import type { StructuredContent } from './api';

/** Keys of `T` whose value is a string. Every scalar on the record is one. */
type StringKeys<T> = {
  [K in keyof T]-?: NonNullable<T[K]> extends string ? K : never;
}[keyof T];

export interface FieldDef<T> {
  key: StringKeys<T>;
  label: string;
  multiline?: boolean;
  rows?: number;
  hint?: string;
}

export interface GroupDef<T> {
  title: string;
  columns?: 1 | 2;
  fields: FieldDef<T>[];
}

/** Identity helper; exists purely so TypeScript infers and checks `T` at each call. */
function group<T>(def: GroupDef<T>): GroupDef<T> {
  return def;
}

type S = StructuredContent;

export const TOP = group<S>({
  title: 'Case',
  columns: 1,
  fields: [
    { key: 'case_title', label: 'Case title' },
    {
      key: 'paragraph_summary',
      label: 'Paragraph summary',
      multiline: true,
      rows: 4,
      hint: 'Opens the Clinical Dashboard. Maps to `presentation` for the framework and LR pipeline.',
    },
  ],
});

export const PATIENT_APPROACH = group<S['patient_approach']>({
  title: 'Patient approach',
  fields: [
    { key: 'education_level', label: 'Education level' },
    { key: 'emotional_response', label: 'Emotional response' },
    {
      key: 'communication_style',
      label: 'Communication style',
      hint: 'Drives the simulator persona.',
    },
  ],
});

export const HPI = group<S['hpi']>({
  title: 'History of present illness (OLDCARTS)',
  fields: [
    { key: 'onset', label: 'Onset' },
    { key: 'location', label: 'Location' },
    { key: 'duration', label: 'Duration' },
    { key: 'character', label: 'Character' },
    { key: 'aggravating_alleviating_factors', label: 'Aggravating / alleviating' },
    { key: 'radiation', label: 'Radiation' },
    { key: 'timing', label: 'Timing' },
    { key: 'severity', label: 'Severity' },
    { key: 'additional_details', label: 'Additional details', multiline: true },
  ],
});

export const PMH = group<S['past_medical_history']>({
  title: 'Past medical history',
  fields: [
    { key: 'active_problems', label: 'Active problems', multiline: true },
    { key: 'inactive_problems', label: 'Inactive problems', multiline: true },
    { key: 'hospitalizations', label: 'Hospitalizations' },
    { key: 'surgical_history', label: 'Surgical history' },
    { key: 'immunizations', label: 'Immunizations' },
  ],
});

export const SOCIAL = group<S['social_history']>({
  title: 'Social history',
  fields: [
    { key: 'tobacco', label: 'Tobacco' },
    { key: 'alcohol', label: 'Alcohol' },
    { key: 'substances', label: 'Substances' },
    { key: 'diet', label: 'Diet' },
    { key: 'exercise', label: 'Exercise' },
    { key: 'sexual_activity', label: 'Sexual activity' },
    { key: 'home_life_safety', label: 'Home life / safety' },
    { key: 'mood', label: 'Mood' },
    { key: 'contextual_details', label: 'Contextual details', multiline: true },
  ],
});

export const FAMILY = group<S['family_history']>({
  title: 'Family history',
  fields: [
    {
      key: 'parents',
      label: 'Parents',
      hint: 'A diagnosis named here is the *parent’s*, but the Oracle leak audit still flags it — override with a reason if so.',
    },
    { key: 'siblings', label: 'Siblings' },
  ],
});

export const MEDS = group<S['medications_allergies']>({
  title: 'Medications and allergies',
  fields: [
    { key: 'medications', label: 'Medications', multiline: true },
    { key: 'allergies', label: 'Allergies' },
  ],
});

export const EXAM_TEXT = group<S>({
  title: 'Review of systems and exam narrative',
  columns: 1,
  fields: [
    { key: 'ros_pertinent_findings', label: 'ROS pertinent findings', multiline: true },
    {
      key: 'physical_exam_findings_text',
      label: 'Physical exam narrative',
      multiline: true,
      rows: 4,
    },
  ],
});

export const REASONING = group<S['diagnostic_reasoning']>({
  title: 'Diagnostic reasoning',
  columns: 1,
  fields: [
    { key: 'essential_hpi_details', label: 'Essential HPI details', multiline: true },
    { key: 'differential_diagnoses', label: 'Differential diagnoses', multiline: true },
    {
      key: 'rationale',
      label: 'Rationale',
      multiline: true,
      rows: 4,
      hint: 'Authoring reasoning. Excluded from the blinded Oracle view — it routinely names the diagnosis (ADR-005).',
    },
  ],
});

export const TEACHING = group<S['teaching_points']>({
  title: 'Teaching points',
  columns: 1,
  fields: [
    { key: 'key_learning_objectives', label: 'Key learning objectives', multiline: true },
    { key: 'educational_content', label: 'Educational content', multiline: true, rows: 4 },
  ],
});

export const DOOR_CHART = group<S['door_chart']>({
  title: 'Door chart',
  fields: [
    { key: 'patient_name', label: 'Patient name' },
    { key: 'age', label: 'Age' },
    { key: 'legal_sex', label: 'Legal sex' },
    { key: 'chief_complaint', label: 'Chief complaint' },
    { key: 'clinical_setting', label: 'Clinical setting' },
  ],
});

export const VITALS = group<S['door_chart']['vital_signs']>({
  title: 'Vital signs',
  fields: [
    { key: 'blood_pressure', label: 'Blood pressure' },
    { key: 'pulse_rate', label: 'Pulse' },
    { key: 'respiratory_rate', label: 'Respiratory rate' },
    { key: 'temperature_celsius', label: 'Temperature (°C)' },
    { key: 'spo2', label: 'SpO₂' },
  ],
});

/** The three dynamic lists. Each item is a pair of strings; labels differ per list. */
export const LISTS = [
  {
    key: 'history_questions' as const,
    title: 'History questions',
    a: { key: 'question' as const, label: 'Question' },
    b: { key: 'expected_answer' as const, label: 'Expected answer' },
  },
  {
    key: 'physical_exam_findings' as const,
    title: 'Physical exam findings',
    a: { key: 'examination' as const, label: 'Examination' },
    b: { key: 'findings' as const, label: 'Findings' },
  },
  {
    key: 'diagnostic_workup' as const,
    title: 'Diagnostic workup',
    a: { key: 'test' as const, label: 'Test' },
    b: { key: 'rationale' as const, label: 'Rationale' },
  },
];
