import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useParams, Link } from '@tanstack/react-router';
import { ArrowLeft } from 'lucide-react';

export function ReportDetailPage() {
  const { reportId } = useParams({ strict: false });

  const { data, isLoading } = useQuery({
    queryKey: ['report', reportId],
    queryFn: async () => {
      const res = await fetch(`/api/reports/${reportId}`);
      if (!res.ok) throw new Error('Network response was not ok');
      return res.json();
    },
  });

  if (isLoading) {
    return <div className="flex h-[50vh] items-center justify-center text-zinc-500">Loading report...</div>;
  }

  if (!data) {
    return <div className="flex h-[50vh] items-center justify-center text-red-500">Report not found</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-4">
        <Link to="/reports" className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-50 transition-colors">
          <ArrowLeft className="h-6 w-6" />
        </Link>
        <h1 className="text-3xl font-bold tracking-tight capitalize">{data.report_type} Report</h1>
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <Card className="md:col-span-1 h-fit">
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <span className="font-semibold block text-zinc-500">Date</span>
              <span>{data.created_at}</span>
            </div>
            {data.ticker && (
              <div>
                <span className="font-semibold block text-zinc-500">Ticker</span>
                <span className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded">{data.ticker}</span>
              </div>
            )}
            <div>
              <span className="font-semibold block text-zinc-500">Summary</span>
              <p className="text-zinc-700 dark:text-zinc-300 mt-1">{data.summary}</p>
            </div>
          </CardContent>
        </Card>

        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Content</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="prose dark:prose-invert max-w-none text-sm">
              <pre className="whitespace-pre-wrap font-sans text-zinc-700 dark:text-zinc-300">
                {data.full_content}
              </pre>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
