import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "@/components/Layout";
import ProtectedRoute from "@/components/ProtectedRoute";
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
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<RaioXPage />} />
              <Route path="/oee"       element={<OeePage />} />
              <Route path="/timeline"  element={<TimelinePage />} />
              <Route path="/stops"     element={<StopsPage />} />
              <Route path="/reports/shift" element={<ShiftReportPage />} />
              <Route path="/settings/production" element={<ProductionSettingsPage />} />
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
