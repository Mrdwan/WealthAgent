import { createRootRoute, createRoute } from '@tanstack/react-router'
import { Outlet } from '@tanstack/react-router'
import { DashboardLayout } from './components/layout'
import { DashboardPage } from './pages/dashboard'
import { ReportsPage } from './pages/reports'
import { ReportDetailPage } from './pages/report-detail'
import { AlertsPage } from './pages/alerts'

const rootRoute = createRootRoute({
  component: () => (
    <DashboardLayout>
      <Outlet />
    </DashboardLayout>
  ),
})

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: DashboardPage,
})

const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reports',
  component: ReportsPage,
})

const reportDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reports/$reportId',
  component: ReportDetailPage,
})

const alertsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/alerts',
  component: AlertsPage,
})

export const routeTree = rootRoute.addChildren([
  indexRoute,
  reportsRoute,
  reportDetailRoute,
  alertsRoute,
])
