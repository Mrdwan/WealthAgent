import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';

export function DashboardPage() {
  const { data: holdingsData, isLoading: isLoadingHoldings } = useQuery({
    queryKey: ['holdings'],
    queryFn: async () => {
      const res = await fetch('/api/holdings');
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  const { data: trackingData, isLoading: isLoadingTracking } = useQuery({
    queryKey: ['tracking-error'],
    queryFn: async () => {
      const res = await fetch('/api/tracking-error');
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  const { data: allocationData, isLoading: isLoadingAllocation } = useQuery({
    queryKey: ['charts', 'allocation'],
    queryFn: async () => {
      const res = await fetch('/api/charts/allocation');
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  if (isLoadingHoldings || isLoadingTracking || isLoadingAllocation) {
    return <div className="flex h-[50vh] items-center justify-center text-zinc-500">Loading dashboard...</div>;
  }

  // Format allocation data for Recharts
  const pieData = allocationData?.labels?.map((label: string, i: number) => ({
    name: label,
    value: allocationData.datasets[0].data[i],
    color: allocationData.datasets[0].backgroundColor[i]
  })) || [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Portfolio Value</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              €{holdingsData?.total_value_eur?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '0.00'}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">30-Day Return</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {trackingData?.portfolio_return_pct !== null ? `${trackingData.portfolio_return_pct}%` : 'N/A'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              vs IWDA: {trackingData?.iwda_return_pct !== null ? `${trackingData.iwda_return_pct}%` : 'N/A'}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Tracking Error</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {trackingData?.tracking_error_pp !== null ? `${trackingData.tracking_error_pp}pp` : 'N/A'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {trackingData?.explanation || 'No data'}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <Card className="col-span-4">
          <CardHeader>
            <CardTitle>Current Holdings</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-xs text-zinc-500 uppercase bg-zinc-50 dark:bg-zinc-800/50">
                  <tr>
                    <th className="px-4 py-3 rounded-tl-lg">Ticker</th>
                    <th className="px-4 py-3 text-right">Shares</th>
                    <th className="px-4 py-3 text-right">Avg Cost</th>
                    <th className="px-4 py-3 text-right">Current</th>
                    <th className="px-4 py-3 text-right">Value</th>
                    <th className="px-4 py-3 text-right rounded-tr-lg">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {holdingsData?.holdings?.map((h: any) => (
                    <tr key={h.ticker} className="border-b dark:border-zinc-800 last:border-0">
                      <td className="px-4 py-3 font-medium">{h.ticker}</td>
                      <td className="px-4 py-3 text-right">{h.shares}</td>
                      <td className="px-4 py-3 text-right">€{h.avg_cost_eur?.toFixed(2)}</td>
                      <td className="px-4 py-3 text-right">€{h.current_price_eur?.toFixed(2)}</td>
                      <td className="px-4 py-3 text-right">€{h.value_eur?.toFixed(2)}</td>
                      <td className={`px-4 py-3 text-right ${h.pnl_pct && h.pnl_pct > 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {h.pnl_pct ? `${h.pnl_pct > 0 ? '+' : ''}${h.pnl_pct.toFixed(2)}%` : '-'}
                      </td>
                    </tr>
                  ))}
                  {(!holdingsData?.holdings || holdingsData.holdings.length === 0) && (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-zinc-500">No holdings found</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card className="col-span-3">
          <CardHeader>
            <CardTitle>Allocation by Pool</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[300px] w-full">
              {pieData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={80}
                      paddingAngle={5}
                      dataKey="value"
                    >
                      {pieData.map((entry: any, index: number) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value: number) => `€${value.toFixed(2)}`} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center text-zinc-500 text-sm">
                  No allocation data available
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
