import { Link, useRouterState } from '@tanstack/react-router'
import { LayoutDashboard, FileText, Bell } from 'lucide-react'
import { cn } from '@/lib/utils'

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouterState()

  const navItems = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'Reports', href: '/reports', icon: FileText },
    { name: 'Alerts', href: '/alerts', icon: Bell },
  ]

  return (
    <div className="flex min-h-screen w-full flex-col bg-zinc-50 dark:bg-zinc-950">
      <aside className="fixed inset-y-0 left-0 z-10 hidden w-64 flex-col border-r bg-white dark:bg-zinc-900 sm:flex">
        <div className="flex h-14 items-center border-b px-4 lg:h-[60px] lg:px-6">
          <span className="font-bold text-xl flex items-center gap-2">
            <span className="text-blue-600">Wealth</span>Agent
          </span>
        </div>
        <div className="flex-1 overflow-auto py-2">
          <nav className="grid items-start px-2 text-sm font-medium lg:px-4">
            {navItems.map((item) => (
              <Link
                key={item.href}
                to={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 transition-all hover:text-zinc-900 dark:hover:text-zinc-50",
                  router.location.pathname === item.href || (item.href !== '/' && router.location.pathname.startsWith(item.href))
                    ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50"
                    : "text-zinc-500 dark:text-zinc-400"
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.name}
              </Link>
            ))}
          </nav>
        </div>
      </aside>
      
      <div className="flex flex-col sm:gap-4 sm:py-4 sm:pl-64">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b bg-white dark:bg-zinc-900 px-4 sm:static sm:h-auto sm:border-0 sm:bg-transparent sm:px-6">
          <div className="relative ml-auto flex-1 md:grow-0">
          </div>
        </header>
        <main className="grid flex-1 items-start gap-4 p-4 sm:px-6 sm:py-0 md:gap-8">
          {children}
        </main>
      </div>
    </div>
  )
}
