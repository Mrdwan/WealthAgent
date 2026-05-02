import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@tanstack/react-router';

export function ReportsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['reports'],
    queryFn: async () => {
      const res = await fetch('/api/reports');
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  if (isLoading) {
    return <div className="flex h-[50vh] items-center justify-center text-zinc-500">Loading reports...</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold tracking-tight">Reports</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent Reports</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-xs text-zinc-500 uppercase bg-zinc-50 dark:bg-zinc-800/50">
                <tr>
                  <th className="px-4 py-3 rounded-tl-lg">Date</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Ticker</th>
                  <th className="px-4 py-3">Summary</th>
                  <th className="px-4 py-3 text-right rounded-tr-lg">Action</th>
                </tr>
              </thead>
              <tbody>
                {data?.reports?.map((r: any) => (
                  <tr key={r.id} className="border-b dark:border-zinc-800 last:border-0 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors">
                    <td className="px-4 py-3 whitespace-nowrap">{r.created_at.split(' ')[0]}</td>
                    <td className="px-4 py-3 font-medium capitalize">{r.report_type}</td>
                    <td className="px-4 py-3">{r.ticker || '-'}</td>
                    <td className="px-4 py-3 text-zinc-500 truncate max-w-[300px]">{r.summary}</td>
                    <td className="px-4 py-3 text-right">
                      <Link 
                        to="/reports/$reportId"
                        params={{ reportId: String(r.id) }}
                        className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 text-sm font-medium"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
                {(!data?.reports || data.reports.length === 0) && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">No reports found</td>
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
