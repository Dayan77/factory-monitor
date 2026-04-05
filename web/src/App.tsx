import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "@/components/Layout";
import ProtectedRoute from "@/components/ProtectedRoute";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/pages/DashboardPage";
import LinesPage from "@/pages/LinesPage";
import MachinesPage from "@/pages/MachinesPage";
import AlertsPage from "@/pages/AlertsPage";
import AiChatPage from "@/pages/AiChatPage";
import SensorsPage from "@/pages/SensorsPage";
import AdminPage from "@/pages/AdminPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/lines"     element={<LinesPage />} />
              <Route path="/machines"  element={<MachinesPage />} />
              <Route path="/sensors"   element={<SensorsPage />} />
              <Route path="/alerts"    element={<AlertsPage />} />
              <Route path="/ai"        element={<AiChatPage />} />
              <Route path="/admin"     element={<AdminPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
