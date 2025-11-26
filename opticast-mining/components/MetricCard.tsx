import React from 'react';

interface MetricCardProps {
  title: string;
  value: string;
  subtitle?: string;
  trend?: 'up' | 'down' | 'neutral';
  trendValue?: string;
  isHighContrast?: boolean;
}

export const MetricCard: React.FC<MetricCardProps> = ({ 
  title, 
  value, 
  subtitle, 
  trend, 
  trendValue,
  isHighContrast = false
}) => {
  return (
    <div className={`p-6 rounded-xl border border-opti-violet/30 shadow-lg backdrop-blur-sm ${
      isHighContrast ? 'bg-opti-violet/40' : 'bg-opti-indigo/50'
    }`}>
      <h3 className="text-opti-cyan font-bold uppercase tracking-wider text-sm mb-2">{title}</h3>
      <div className="flex items-end justify-between">
        <span className="text-3xl font-bold text-white tracking-tight">{value}</span>
        {trend && (
          <div className={`flex items-center text-sm font-bold px-2 py-1 rounded ${
            trend === 'up' ? 'bg-red-500/20 text-red-400' : 
            trend === 'down' ? 'bg-opti-green/20 text-opti-green' : 
            'bg-gray-500/20 text-gray-300'
          }`}>
            <span>{trend === 'up' ? '▲' : trend === 'down' ? '▼' : '−'}</span>
            <span className="ml-1">{trendValue}</span>
          </div>
        )}
      </div>
      {subtitle && <p className="text-gray-400 text-xs mt-2">{subtitle}</p>}
    </div>
  );
};