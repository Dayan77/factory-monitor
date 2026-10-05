import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Routes, Route, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "@/components/Layout";
import ProtectedRoute from "@/components/ProtectedRoute";
import RequireCapability from "@/components/RequireCapability";
import LandingPage from "@/pages/LandingPage";
import LoginPage from "@/pages/LoginPage";
import RegisterPage from "@/pages/RegisterPage";
import RaioXPage from "@/pages/efficiency/RaioXPage";
import StopsPage from "@/pages/efficiency/StopsPage";
import LinesPage from "@/pages/LinesPage";
import MachinesPage from "@/pages/MachinesPage";
import AlertsPage from "@/pages/AlertsPage";
const AiChatPage = lazy(() => import("@/pages/AiChatPage"));
const SensorsPage = lazy(() => import("@/pages/SensorsPage"));
const AdminPage = lazy(() => import("@/pages/AdminPage"));
const CamerasPage = lazy(() => import("@/pages/CamerasPage"));
const FactoryPage = lazy(() => import("@/pages/factory/FactoryPage"));
const LinePage = lazy(() => import("@/pages/factory/LinePage"));
const MachinePage = lazy(() => import("@/pages/factory/MachinePage"));
const OeePage = lazy(() => import("@/pages/efficiency/OeePage"));
const TimelinePage = lazy(() => import("@/pages/efficiency/TimelinePage"));
const ShiftReportPage = lazy(() => import("@/pages/efficiency/ShiftReportPage"));
const TvPage = lazy(() => import("@/pages/efficiency/TvPage"));
const ProductionSettingsPage = lazy(() => import("@/pages/settings/ProductionSettingsPage"));
const UpcomingPage = lazy(() => import("@/pages/maintenance/UpcomingPage"));
const ComponentsLifePage = lazy(() => import("@/pages/maintenance/ComponentsLifePage"));
const WorkOrdersPage = lazy(() => import("@/pages/maintenance/WorkOrdersPage"));
const SchedulePage = lazy(() => import("@/pages/maintenance/SchedulePage"));
const FailuresPage = lazy(() => import("@/pages/maintenance/FailuresPage"));
const PlansSettingsPage = lazy(() => import("@/pages/maintenance/PlansSettingsPage"));
const KpisPage = lazy(() => import("@/pages/maintenance/KpisPage"));
const ReliabilityPage = lazy(() => import("@/pages/maintenance/ReliabilityPage"));
const MobileLayout = lazy(() => import("@/pages/mobile/MobileLayout"));
const MyWorkOrdersPage = lazy(() => import("@/pages/mobile/MyWorkOrdersPage"));
const WorkOrderExecPage = lazy(() => import("@/pages/mobile/WorkOrderExecPage"));
const TodayLogPage = lazy(() => import("@/pages/mobile/TodayLogPage"));

/** Deep links used by notifications (/maintenance/work-orders/:id) → the screen with its detail open. */
function DetailRedirect({ to }: { to: (id: string) => string }) {
  const { id = "" } = useParams();
  return <Navigate to={to(id)} replace />;
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

function RouteFallback() {
  return (
    <div role="status" aria-busy="true" className="flex min-h-[50vh] items-center justify-center">
      <span className="sr-only">Carregando…</span>
      <span className="size-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Suspense fallback={<RouteFallback />}>
        <Routes>
          {/* Páginas públicas */}
          <Route path="/"         element={<LandingPage />} />
          <Route path="/login"    element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* App protegido */}
          <Route element={<ProtectedRoute />}>
            {/* Andon / TV: full screen, no app chrome */}
            <Route path="/tv/lines/:lineId" element={<TvPage />} />
            {/* Técnico de manutenção: app de celular (PWA) */}
            <Route path="/m" element={<MobileLayout />}>
              <Route index element={<Navigate to="/m/work-orders" replace />} />
              <Route path="work-orders" element={<MyWorkOrdersPage />} />
              <Route path="work-orders/:id" element={<WorkOrderExecPage />} />
              <Route path="log" element={<TodayLogPage />} />
            </Route>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<RaioXPage />} />
              <Route path="/oee"       element={<OeePage />} />
              <Route path="/timeline"  element={<TimelinePage />} />
              <Route path="/stops"     element={<StopsPage />} />
              <Route path="/reports/shift" element={<ShiftReportPage />} />
              <Route path="/settings/production" element={<ProductionSettingsPage />} />
              <Route path="/settings/maintenance-plans" element={<RequireCapability cap="maintenance.plan"><PlansSettingsPage /></RequireCapability>} />
              <Route path="/maintenance" element={<Navigate to="/maintenance/work-orders" replace />} />
              <Route path="/maintenance/upcoming" element={<RequireCapability cap="maintenance.plan"><UpcomingPage /></RequireCapability>} />
              <Route path="/maintenance/components" element={<RequireCapability cap="maintenance.plan"><ComponentsLifePage /></RequireCapability>} />
              <Route path="/maintenance/work-orders" element={<RequireCapability cap="maintenance.view"><WorkOrdersPage /></RequireCapability>} />
              <Route path="/maintenance/schedule" element={<RequireCapability cap="maintenance.plan"><SchedulePage /></RequireCapability>} />
              <Route path="/maintenance/work-orders/:id" element={<DetailRedirect to={(id) => `/maintenance/work-orders?wo=${encodeURIComponent(id)}`} />} />
              <Route path="/maintenance/failures/:id" element={<DetailRedirect to={(id) => `/maintenance/failures?f=${encodeURIComponent(id)}`} />} />
              <Route path="/maintenance/failures" element={<RequireCapability cap="maintenance.failures"><FailuresPage /></RequireCapability>} />
              <Route path="/maintenance/kpis" element={<RequireCapability cap="maintenance.kpis"><KpisPage /></RequireCapability>} />
              <Route path="/maintenance/reliability" element={<RequireCapability cap="maintenance.kpis"><ReliabilityPage /></RequireCapability>} />
              <Route path="/factory"   element={<FactoryPage />} />
              <Route path="/factory/lines/:lineId" element={<LinePage />} />
              <Route path="/factory/machines/:machineId" element={<MachinePage />} />
              <Route path="/lines"     element={<LinesPage />} />
              <Route path="/machines"  element={<MachinesPage />} />
              <Route path="/sensors"   element={<SensorsPage />} />
              <Route path="/cameras"   element={<CamerasPage />} />
              <Route path="/alerts"    element={<AlertsPage />} />
              <Route path="/ai"        element={<AiChatPage />} />
              <Route path="/admin"     element={<AdminPage />} />
            </Route>
          </Route>
        </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
