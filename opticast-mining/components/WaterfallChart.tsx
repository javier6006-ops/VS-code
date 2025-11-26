import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';

interface WaterfallData {
  name: string;
  uv: number; // The actual value height
  start: number; // The visual offset
  isTotal?: boolean;
  color: string;
}

interface Props {
  data: any[];
}

export const WaterfallChart: React.FC<Props> = ({ data }) => {
  // Transform simplified data into waterfall structure
  // Assuming data comes in as [ {name: 'Base', value: 100}, {name: 'Labor', value: 5}, ... {name: 'Total', value: 105}]
  
  // MOCKING logic for visual demonstration based on the user's waterfall request
  // In a real app, this transformation happens in the service layer
  
  const formatCurrency = (val: number) => `$${(val/1000000).toFixed(1)}M`;

  return (
    <div className="w-full h-[400px] bg-opti-indigo/30 p-4 rounded-xl border border-opti-violet/30">
      <h4 className="text-white font-semibold mb-4 flex items-center">
        <span className="w-2 h-6 bg-opti-cyan mr-2 rounded-sm"></span>
        Bridge de Variaciones (EBITDA Impact)
      </h4>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#4A148C" opacity={0.3} vertical={false} />
          <XAxis 
            dataKey="name" 
            stroke="#A5B4FC" 
            fontSize={12} 
            tickLine={false}
          />
          <YAxis 
            stroke="#A5B4FC" 
            fontSize={12} 
            tickFormatter={formatCurrency}
            tickLine={false}
          />
          <Tooltip 
            cursor={{fill: '#ffffff10'}}
            contentStyle={{ backgroundColor: '#1A237E', borderColor: '#00E5FF', color: '#fff' }}
            formatter={(value: number) => [`$${value.toLocaleString()}`, 'Monto']}
          />
          <ReferenceLine y={0} stroke="#00E5FF" />
          
          {/* The "Invisible" Base Bar to make others float */}
          <Bar dataKey="start" stackId="a" fill="transparent" />
          
          {/* The Actual Value Bar */}
          <Bar dataKey="value" stackId="a" radius={[4, 4, 4, 4]}>
            {data.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};