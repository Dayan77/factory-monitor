import { useEffect } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useAuthStore } from "@/store/auth";

export default function ProtectedRoute() {
  const { user, loading, loadMe } = useAuthStore();

  useEffect(() => {
    if (!user) loadMe();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950">
        <span className="size-6 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
      </div>
    );
  }

  const token = localStorage.getItem("access_token");
  if (!token && !user) return <Navigate to="/login" replace />;

  return <Outlet />;
}
