import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "@/components/Layout";
import DashboardPage from "@/pages/DashboardPage";
import LinesPage from "@/pages/LinesPage";
import MachinesPage from "@/pages/MachinesPage";
import AlertsPage from "@/pages/AlertsPage";
import AiChatPage from "@/pages/AiChatPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/lines" element={<LinesPage />} />
          <Route path="/machines" element={<MachinesPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/ai" element={<AiChatPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
