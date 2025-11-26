export enum Frequency {
  WEEKLY = 'Weekly',
  MONTHLY = 'Monthly',
  ANNUAL = 'Annual'
}

export enum AnalysisType {
  STANDARD = 'Standard',
  CUSTOM = 'Custom'
}

export interface ConfigParams {
  historicalPeriods: number;
  forecastPeriods: number;
  budgetPeriods: number;
  frequency: Frequency;
}

export interface FinancialDriver {
  item: string;
  factor: number;
  justification: string;
}

export interface KPIData {
  forecastAmount: number;
  budgetAmount: number;
  variance: number;
}

export interface ChartDataPoint {
  name: string;
  value: number;
  type?: 'base' | 'increase' | 'decrease' | 'total';
  start?: number; // For waterfall base
}

export interface DetailedRow {
  category: string;
  item: string;
  forecast: number[];
  budget: number[];
  totalBudget: number;
}