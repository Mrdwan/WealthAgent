import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function AlertsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['alerts'],
    queryFn: async () => {
      const res = await fetch('/api/alerts');
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  if (isLoading) {
    return <div className="flex h-[50vh] items-center justify-center text-zinc-500">Loading alerts...</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold tracking-tight">Alerts</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent Alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-xs text-zinc-500 uppercase bg-zinc-50 dark:bg-zinc-800/50">
                <tr>
                  <th className="px-4 py-3 rounded-tl-lg">Triggered At</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Ticker</th>
                  <th className="px-4 py-3 rounded-tr-lg">Details</th>
                </tr>
              </thead>
              <tbody>
                {data?.alerts?.map((a: any) => (
                  <tr key={a.id} className="border-b dark:border-zinc-800 last:border-0 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                    <td className="px-4 py-3 whitespace-nowrap">{a.triggered_at.replace('T', ' ').split('.')[0]}</td>
                    <td className="px-4 py-3 font-medium capitalize">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        a.alert_type === 'price_drop' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' :
                        a.alert_type === 'news_signal' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' :
                        'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300'
                      }`}>
                        {a.alert_type.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono">{a.ticker}</td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{a.details}</td>
                  </tr>
                ))}
                {(!data?.alerts || data.alerts.length === 0) && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-zinc-500">No recent alerts found</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
