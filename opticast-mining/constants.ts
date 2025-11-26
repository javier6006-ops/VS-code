import { FinancialDriver } from './types';

export const APP_NAME = "OptiCast Mining";
export const APP_SLOGAN = "Proyección inteligente, optimización real.";

export const DEFAULT_DRIVERS: FinancialDriver[] = [
  { item: "Contractors", factor: 1.015, justification: "VARIACIÓN CONTROLADA: Eficiencia Modelo Minex Quantum." },
  { item: "Labor", factor: 1.042, justification: "ALINEACIÓN IPC + RETENCIÓN: 3.8% + 0.4%." },
  { item: "Fuel", factor: 1.050, justification: "PRODUCCIÓN: Aumento plan movimiento tierra." },
  { item: "S&C", factor: 1.038, justification: "MERCADO: Indexación insumos críticos." },
  { item: "Power", factor: 1.060, justification: "TARIFAS: Alza tarifas reguladas 2026." },
  { item: "Maintenance", factor: 1.030, justification: "DISPONIBILIDAD: Estrategia predictiva." },
  { item: "Otros", factor: 1.035, justification: "AJUSTE ESTÁNDAR: Inflación promedio." }
];

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];