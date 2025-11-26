import * as XLSX from 'xlsx';
import { AnalysisType, ConfigParams } from './types';
import { APP_NAME, DEFAULT_DRIVERS } from './constants';

export const generateExcelReport = (
    analysisType: AnalysisType,
    params: ConfigParams | undefined,
    tableData: any[],
    trendData: any[],
    waterfallData: any[]
) => {
    // Create Workbook
    const wb = XLSX.utils.book_new();

    // --- SHEET 1: RESUMEN GERENCIAL ---
    // Extract totals for top summary
    const totalForecast = waterfallData.find((d: any) => d.name.includes('FCST'))?.value || 0;
    const totalBudget = waterfallData.find((d: any) => d.name.includes('BUD'))?.value || 0;
    const diff = totalBudget - totalForecast;

    const summaryData = [
        ["REPORTE FINANCIERO - " + APP_NAME.toUpperCase()],
        ["Fecha de Generación", new Date().toLocaleString()],
        ["Tipo de Análisis", analysisType === AnalysisType.STANDARD ? "Estándar (Automático)" : "Personalizado"],
        [],
        ["KPIs PRINCIPALES (USD M)"],
        ["Concepto", "Monto", "Estado"],
        ["Forecast 2025 (Cierre)", totalForecast, "Base"],
        ["Budget 2026 (Target)", totalBudget, "Meta"],
        ["Variación YoY", diff, diff > 0 ? "Incremento Costo" : "Ahorro"],
        [],
        ["--- DATA PARA GRÁFICO CASCADA (BRIDGE) ---"],
        ["La siguiente tabla contiene los datos calculados para generar el gráfico de cascada en Excel."],
        [],
        ["Categoría / Paso", "Valor Absoluto", "Acumulado Inicio", "Tipo de Movimiento"],
        ...waterfallData.map((d: any) => [
            d.name, 
            d.isTotal ? d.value : (d.displayValue || d.value), // Use displayValue (signed) if available
            d.start,
            d.isTotal ? "Total" : (d.displayValue > 0 ? "Incremento" : "Disminución")
        ])
    ];

    const wsSummary = XLSX.utils.aoa_to_sheet(summaryData);
    
    // Set column widths for Summary
    wsSummary['!cols'] = [{ wch: 30 }, { wch: 20 }, { wch: 20 }, { wch: 20 }];
    
    XLSX.utils.book_append_sheet(wb, wsSummary, "Resumen_Gerencial");

    // --- SHEET 2: MATRIZ DETALLE (The Table) ---
    const tableHeader = ["Categoría Driver", "Ítem Detalle", "Forecast 2025 (M$)", "Budget 2026 (M$)", "Factor Aplicado", "Variación %", "Variación Abs (M$)"];
    const tableRows = tableData.map((row: any) => [
        row.cat,
        row.item,
        row.f25,
        row.b26,
        row.factor,
        ((row.b26 / row.f25) - 1), // Format as percentage in Excel manually if needed
        row.b26 - row.f25
    ]);

    const wsDetalle = XLSX.utils.aoa_to_sheet([tableHeader, ...tableRows]);
    wsDetalle['!cols'] = [{ wch: 20 }, { wch: 35 }, { wch: 15 }, { wch: 15 }, { wch: 15 }, { wch: 15 }, { wch: 20 }];
    XLSX.utils.book_append_sheet(wb, wsDetalle, "Matriz_Detalle");

    // --- SHEET 3: PLAN LARGO PLAZO (Trends) ---
    const ltpHeader = ["Año", "Proyección Total (M$)"];
    const ltpRows = trendData.map((d: any) => [d.year, d.value]);
    const wsLTP = XLSX.utils.aoa_to_sheet([ltpHeader, ...ltpRows]);
    wsLTP['!cols'] = [{ wch: 15 }, { wch: 20 }];
    XLSX.utils.book_append_sheet(wb, wsLTP, "Plan_Largo_Plazo");

    // --- SHEET 4: SUPUESTOS & CONFIGURACIÓN ---
    const configData = [
        ["CONFIGURACIÓN DE PROYECCIÓN"],
        ["Parámetro", "Valor Seleccionado"],
        ["Periodos Históricos", params?.historicalPeriods || 12],
        ["Periodos Forecast", params?.forecastPeriods || 12],
        ["Periodos Budget", params?.budgetPeriods || 4],
        ["Frecuencia", params?.frequency || "Mensual/Anual"],
        [],
        ["DRIVERS ESTRATÉGICOS APLICADOS"],
        ["Item / Driver", "Factor", "Justificación"],
        ...DEFAULT_DRIVERS.map(d => [d.item, d.factor, d.justification])
    ];

    const wsConfig = XLSX.utils.aoa_to_sheet(configData);
    wsConfig['!cols'] = [{ wch: 25 }, { wch: 15 }, { wch: 50 }];
    XLSX.utils.book_append_sheet(wb, wsConfig, "Supuestos");

    // --- WRITE FILE ---
    XLSX.writeFile(wb, `OptiCast_Analysis_${new Date().toISOString().slice(0,10)}.xlsx`);
};